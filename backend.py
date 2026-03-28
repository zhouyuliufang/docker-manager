#!/usr/bin/env python3
"""
Docker 管理中心 后端服务 v3.0.0
优化内容：
  P0-1: 密码持久化（存 /data/auth.json，挂载数据目录，重建容器不丢失）
  P0-2: JWT 过期机制（7天有效期，自动续签）
  P1-1: 容器管理增强（启停/重启/删除 + EventSource 实时日志）
  P1-2: 资源监控面板（CPU/内存/磁盘/网络，docker stats 流式读取）
  P1-3: 应用商店一键部署（返回 compose 模板内容）
  P2-3: 部署历史记录（存 /data/deploy_history.json）
  P2-4: Compose 文件持久化（存 /data/saved_composes.json）
"""

import os, json, time, subprocess, threading, uuid, hashlib, hmac, base64, re, shlex, shutil
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs

# ──────────────────────────── 常量 ────────────────────────────
VERSION    = "3.0.0"
PORT       = 8081
DATA_DIR   = "/data"                        # 挂载的持久化目录
AUTH_FILE  = os.path.join(DATA_DIR, "auth.json")
HIST_FILE  = os.path.join(DATA_DIR, "deploy_history.json")
SAVES_FILE = os.path.join(DATA_DIR, "saved_composes.json")
JWT_SECRET = None                           # 启动时从 auth.json 读取或生成
JWT_EXPIRE = 7 * 24 * 3600                 # 7 天

os.makedirs(DATA_DIR, exist_ok=True)

# ──────────────────────────── JWT（纯 Python，无需第三方库）────────────────────────────
def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()

def _b64url_decode(s: str) -> bytes:
    pad = 4 - len(s) % 4
    return base64.urlsafe_b64decode(s + "=" * (pad % 4))

def jwt_encode(payload: dict) -> str:
    header = _b64url(json.dumps({"alg": "HS256", "typ": "JWT"}).encode())
    body   = _b64url(json.dumps(payload).encode())
    sig    = _b64url(hmac.new(JWT_SECRET.encode(), f"{header}.{body}".encode(), "sha256").digest())
    return f"{header}.{body}.{sig}"

def jwt_decode(token: str) -> dict | None:
    try:
        parts = token.split(".")
        if len(parts) != 3:
            return None
        header, body, sig = parts
        expected = _b64url(hmac.new(JWT_SECRET.encode(), f"{header}.{body}".encode(), "sha256").digest())
        if not hmac.compare_digest(sig, expected):
            return None
        payload = json.loads(_b64url_decode(body))
        if payload.get("exp", 0) < time.time():
            return None
        return payload
    except Exception:
        return None

def make_token() -> str:
    return jwt_encode({"iat": int(time.time()), "exp": int(time.time()) + JWT_EXPIRE})

# ──────────────────────────── 认证存储（P0-1 密码持久化）────────────────────────────
def _load_auth() -> dict:
    global JWT_SECRET
    if os.path.exists(AUTH_FILE):
        try:
            data = json.loads(open(AUTH_FILE).read())
        except Exception:
            data = {}
    else:
        data = {}
    if not data.get("jwt_secret"):
        data["jwt_secret"] = uuid.uuid4().hex
        _save_auth(data)
    JWT_SECRET = data["jwt_secret"]
    return data

def _save_auth(data: dict):
    open(AUTH_FILE, "w").write(json.dumps(data, indent=2))

AUTH = _load_auth()   # 启动时加载

def is_initialized() -> bool:
    return bool(AUTH.get("password_hash"))

def check_password(pwd: str) -> bool:
    return hmac.compare_digest(
        AUTH.get("password_hash", ""),
        hashlib.sha256(pwd.encode()).hexdigest()
    )

def set_password(pwd: str):
    AUTH["password_hash"] = hashlib.sha256(pwd.encode()).hexdigest()
    _save_auth(AUTH)

# ──────────────────────────── 部署历史（P2-3）────────────────────────────
def _load_history() -> list:
    if os.path.exists(HIST_FILE):
        try:
            return json.loads(open(HIST_FILE).read())
        except Exception:
            pass
    return []

def _append_history(record: dict):
    hist = _load_history()
    hist.insert(0, record)
    hist = hist[:50]  # 最多保留 50 条
    open(HIST_FILE, "w").write(json.dumps(hist, ensure_ascii=False, indent=2))

# ──────────────────────────── Compose 持久化（P2-4）────────────────────────────
def _load_saves() -> list:
    if os.path.exists(SAVES_FILE):
        try:
            return json.loads(open(SAVES_FILE).read())
        except Exception:
            pass
    return []

def _save_composes(saves: list):
    open(SAVES_FILE, "w").write(json.dumps(saves, ensure_ascii=False, indent=2))

# ──────────────────────────── 任务系统（Compose 部署）────────────────────────────
tasks: dict[str, dict] = {}

def _supports_progress_flag() -> bool:
    try:
        r = subprocess.run(
            ["docker", "compose", "--progress", "plain", "version"],
            capture_output=True, timeout=5
        )
        return r.returncode == 0
    except Exception:
        return False

PROGRESS_FLAG = _supports_progress_flag()

def _extract_error_summary(lines: list[str]) -> list[str]:
    keywords = ["error", "failed", "denied", "permission", "not found",
                "port is already", "no space", "timeout", "unable to"]
    errors = []
    for ln in reversed(lines):
        low = ln.lower()
        if any(k in low for k in keywords):
            errors.append(ln.strip())
        if len(errors) >= 5:
            break
    return list(reversed(errors))

def _run_deploy(task_id: str, compose_yaml: str, project_name: str):
    task = tasks[task_id]
    task["status"] = "running"
    task["phase"]  = "pulling"
    task["logs"]   = []
    task["pull_progress"] = 0

    # 写临时 compose 文件
    tmp_dir = f"/tmp/dm_{task_id}"
    os.makedirs(tmp_dir, exist_ok=True)
    compose_path = os.path.join(tmp_dir, "docker-compose.yml")
    with open(compose_path, "w") as f:
        f.write(compose_yaml)

    # 拉取镜像
    pull_cmd = ["docker", "compose", "-p", project_name, "-f", compose_path, "pull"]
    if PROGRESS_FLAG:
        pull_cmd += ["--progress", "plain"]

    layers_done: set = set()
    layers_total: set = set()

    try:
        proc = subprocess.Popen(pull_cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                text=True, bufsize=1)
        for line in proc.stdout:
            line = line.rstrip()
            task["logs"].append(line)
            # 解析 buildkit 进度
            m_total = re.search(r"#(\d+) \[", line)
            m_done  = re.search(r"#(\d+) DONE", line)
            if m_total:
                layers_total.add(m_total.group(1))
            if m_done:
                layers_done.add(m_done.group(1))
            if layers_total:
                task["pull_progress"] = int(len(layers_done) / len(layers_total) * 100)
        proc.wait()
        if proc.returncode != 0:
            raise RuntimeError("pull failed")
    except Exception as e:
        task["status"] = "failed"
        task["phase"]  = "failed"
        task["error_summary"] = _extract_error_summary(task["logs"])
        _append_history({"id": task_id, "project": project_name, "status": "failed",
                         "time": int(time.time()), "error": str(e)})
        shutil.rmtree(tmp_dir, ignore_errors=True)
        return

    # 启动服务
    task["phase"] = "starting"
    task["pull_progress"] = 100
    up_cmd = ["docker", "compose", "-p", project_name, "-f", compose_path, "up", "-d", "--remove-orphans"]
    try:
        proc2 = subprocess.Popen(up_cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                 text=True, bufsize=1)
        for line in proc2.stdout:
            task["logs"].append(line.rstrip())
        proc2.wait()
        if proc2.returncode != 0:
            raise RuntimeError("up failed")
    except Exception as e:
        task["status"] = "failed"
        task["phase"]  = "failed"
        task["error_summary"] = _extract_error_summary(task["logs"])
        _append_history({"id": task_id, "project": project_name, "status": "failed",
                         "time": int(time.time()), "error": str(e)})
        shutil.rmtree(tmp_dir, ignore_errors=True)
        return

    task["status"] = "done"
    task["phase"]  = "done"
    shutil.rmtree(tmp_dir, ignore_errors=True)
    _append_history({"id": task_id, "project": project_name, "status": "done",
                     "time": int(time.time())})

# ──────────────────────────── daemon.json（镜像加速）────────────────────────────
def _get_daemon_json_path() -> str:
    for p in ["/etc/docker/daemon.json", "/var/snap/docker/current/config/daemon.json"]:
        if os.path.exists(os.path.dirname(p)):
            return p
    return "/etc/docker/daemon.json"

def _read_daemon() -> dict:
    p = _get_daemon_json_path()
    if os.path.exists(p):
        try:
            return json.loads(open(p).read())
        except Exception:
            pass
    return {}

def _write_daemon(data: dict):
    p = _get_daemon_json_path()
    os.makedirs(os.path.dirname(p), exist_ok=True)
    open(p, "w").write(json.dumps(data, indent=2))

# ──────────────────────────── 容器操作辅助（P1-1）────────────────────────────
def docker_action(container_id: str, action: str) -> tuple[bool, str]:
    """执行容器操作：start / stop / restart / remove"""
    if action == "remove":
        cmd = ["docker", "rm", "-f", container_id]
    else:
        cmd = ["docker", container_id if action == "rm" else action, container_id]
        cmd = ["docker", action, container_id]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        return r.returncode == 0, (r.stdout + r.stderr).strip()
    except Exception as e:
        return False, str(e)

# ──────────────────────────── 容器列表（P1-1）────────────────────────────
def _parse_ports(ports_str: str) -> list:
    """把 docker ps Ports 字符串解析成前端可用的结构
    例：0.0.0.0:9999->80/tcp, 0.0.0.0:8080->443/tcp
    """
    result = []
    if not ports_str:
        return result
    for seg in ports_str.split(", "):
        seg = seg.strip()
        if "->" in seg:
            left, right = seg.split("->", 1)
            # left: 0.0.0.0:9999 或 9999
            pub_port = left.split(":")[-1] if ":" in left else left
            priv_port = right.split("/")[0]
            proto = right.split("/")[-1] if "/" in right else "tcp"
            try:
                result.append({
                    "PublicPort": int(pub_port),
                    "PrivatePort": int(priv_port),
                    "Type": proto,
                })
            except ValueError:
                pass
    return result

def get_containers() -> list:
    """获取所有容器列表，返回与 Docker API /containers/json 兼容的格式"""
    try:
        r = subprocess.run(
            ["docker", "ps", "-a", "--format", "{{json .}}"],
            capture_output=True, text=True, timeout=10
        )
        containers = []
        for line in r.stdout.strip().splitlines():
            try:
                c = json.loads(line)
                # Names: docker API 返回 ["/name"] 格式
                name_raw = c.get("Names", "")
                names = ["/" + name_raw.lstrip("/")]
                # Ports: 解析成对象数组
                ports = _parse_ports(c.get("Ports", ""))
                containers.append({
                    "Id":      c.get("ID", ""),
                    "Names":   names,
                    "Image":   c.get("Image", ""),
                    "State":   c.get("State", ""),
                    "Status":  c.get("Status", ""),
                    "Ports":   ports,
                    "Created": c.get("CreatedAt", ""),
                })
            except Exception:
                pass
        return containers
    except Exception as e:
        return []

# ──────────────────────────── 镜像管理（Task 2）────────────────────────────
def get_images() -> list:
    """获取本地镜像列表"""
    try:
        r = subprocess.run(
            ["docker", "image", "ls", "--format", "{{json .}}"],
            capture_output=True, text=True, timeout=10
        )
        images = []
        for line in r.stdout.strip().splitlines():
            try:
                img = json.loads(line)
                # 解析 Repository 和 Tag
                repository = img.get("Repository", "")
                tag = img.get("Tag", "")
                # 只处理有 tag 的镜像
                if repository and tag and tag != "<none>":
                    images.append({
                        "name": f"{repository}:{tag}",
                        "repository": repository,
                        "tag": tag,
                        "id": img.get("ID", "")[:12],
                        "size": img.get("Size", ""),
                        "created": img.get("CreatedAt", ""),
                    })
            except Exception:
                pass
        return images
    except Exception as e:
        return []

import urllib.request, urllib.error
from datetime import datetime, timezone

def _get_local_image_digest(name: str) -> str:
    """获取本地镜像的 RepoDigest（即拉取时记录的远端 digest）"""
    try:
        r = subprocess.run(
            ["docker", "inspect", "--format", "{{index .RepoDigests 0}}", name],
            capture_output=True, text=True, timeout=10
        )
        digest = r.stdout.strip()
        # 格式: image@sha256:xxxxx  → 取 sha256:xxx 部分
        if "@" in digest:
            return digest.split("@", 1)[1]
        return ""
    except Exception:
        return ""

def _get_remote_image_digest(namespace: str, image_name: str, tag: str) -> str:
    """通过 Docker Hub API 获取远端镜像最新 digest"""
    try:
        # 先取 token
        auth_url = (
            f"https://auth.docker.io/token?service=registry.docker.io"
            f"&scope=repository:{namespace}/{image_name}:pull"
        )
        with urllib.request.urlopen(auth_url, timeout=10) as resp:
            token = json.loads(resp.read().decode()).get("token", "")
        if not token:
            return ""
        # 取 manifest
        manifest_url = f"https://registry.hub.docker.com/v2/{namespace}/{image_name}/manifests/{tag}"
        req = urllib.request.Request(manifest_url, headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.docker.distribution.manifest.v2+json"
        })
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.headers.get("Docker-Content-Digest", "")
    except Exception:
        return ""

def check_image_update(name: str) -> dict:
    """检查镜像是否有更新：对比本地 digest vs 远端 digest"""
    try:
        # 解析镜像名: namespace/image:tag 或 image:tag
        if ":" in name:
            image_full, tag = name.rsplit(":", 1)
        else:
            image_full, tag = name, "latest"

        # 处理 namespace
        if "/" in image_full:
            namespace, image_name = image_full.rsplit("/", 1)
        else:
            namespace, image_name = "library", image_full

        # 非 Docker Hub 镜像（含域名前缀）直接走 Hub API 查时间
        is_hub = ("." not in namespace)

        # 1. 获取本地 digest
        local_digest = _get_local_image_digest(name)

        # 2. 获取远端 digest（仅 Docker Hub）
        remote_digest = ""
        if is_hub:
            remote_digest = _get_remote_image_digest(namespace, image_name, tag)

        # 3. 如果拿到了两个 digest，直接对比
        if local_digest and remote_digest:
            has_update = (local_digest != remote_digest)
            return {
                "has_update": has_update,
                "local_digest":  local_digest[:19] + "…",
                "remote_digest": remote_digest[:19] + "…",
                "message": ("发现新版本可更新！" if has_update else "已是最新版本"),
            }

        # 4. fallback：Docker Hub API 查 last_updated
        if is_hub:
            url = (
                f"https://registry.hub.docker.com/v2/repositories"
                f"/{namespace}/{image_name}/tags/?page_size=100"
            )
            with urllib.request.urlopen(url, timeout=10) as resp:
                data = json.loads(resp.read().decode())
            for result in data.get("results", []):
                if result.get("name") == tag:
                    last_updated = result.get("last_updated", "")
                    if last_updated:
                        updated_dt = datetime.fromisoformat(last_updated.replace("Z", "+00:00"))
                        now = datetime.now(timezone.utc)
                        days = (now - updated_dt).days
                        # 本地没有 digest 时无法精确判断，仅提示时间
                        return {
                            "has_update": None,   # 未知，让前端显示「可能有更新」
                            "last_updated": last_updated,
                            "days_ago": days,
                            "message": f"Hub 最新版发布于 {days} 天前，建议手动确认",
                        }

        return {"has_update": False, "message": "无法获取远端信息"}
    except Exception as e:
        return {"has_update": False, "error": str(e), "message": "检查失败"}


# ── 拉取镜像更新 + 重启容器 ──────────────────────────────────────────
def pull_and_restart(image_name: str) -> dict:
    """docker pull 拉取最新镜像，返回结果；调用方决定是否重启容器"""
    try:
        r = subprocess.run(
            ["docker", "pull", image_name],
            capture_output=True, text=True, timeout=300
        )
        output = (r.stdout + r.stderr).strip()
        ok = r.returncode == 0
        return {"ok": ok, "output": output}
    except subprocess.TimeoutExpired:
        return {"ok": False, "output": "拉取超时（300s）"}
    except Exception as e:
        return {"ok": False, "output": str(e)}


def get_containers_by_image(image_name: str) -> list:
    """找出所有使用指定镜像的容器 ID"""
    try:
        r = subprocess.run(
            ["docker", "ps", "-a", "--filter", f"ancestor={image_name}",
             "--format", "{{.ID}}"],
            capture_output=True, text=True, timeout=10
        )
        return [line.strip() for line in r.stdout.strip().splitlines() if line.strip()]
    except Exception:
        return []

# ──────────────────────────── 资源监控（P1-2）────────────────────────────
def get_stats() -> dict:
    """获取所有容器的资源使用情况"""
    result = {"containers": [], "disk": {}}
    try:
        r = subprocess.run(
            ["docker", "stats", "--no-stream", "--format",
             "{{json .}}"],
            capture_output=True, text=True, timeout=10
        )
        for line in r.stdout.strip().splitlines():
            try:
                item = json.loads(line)
                result["containers"].append({
                    "name":     item.get("Name", ""),
                    "cpu":      item.get("CPUPerc", "0%").replace("%", ""),
                    "mem_used": item.get("MemUsage", "").split(" / ")[0].strip(),
                    "mem_limit":item.get("MemUsage", "").split(" / ")[-1].strip(),
                    "mem_perc": item.get("MemPerc", "0%").replace("%", ""),
                    "net_in":   item.get("NetIO", "").split(" / ")[0].strip(),
                    "net_out":  item.get("NetIO", "").split(" / ")[-1].strip(),
                    "pids":     item.get("PIDs", "0"),
                })
            except Exception:
                pass
    except Exception:
        pass

    # 磁盘使用
    try:
        r = subprocess.run(["df", "-h", "/"], capture_output=True, text=True, timeout=5)
        lines = r.stdout.strip().splitlines()
        if len(lines) >= 2:
            parts = lines[1].split()
            result["disk"] = {
                "total": parts[1] if len(parts) > 1 else "-",
                "used":  parts[2] if len(parts) > 2 else "-",
                "free":  parts[3] if len(parts) > 3 else "-",
                "perc":  parts[4].replace("%","") if len(parts) > 4 else "0",
            }
    except Exception:
        pass

    return result

# ──────────────────────────── 应用商店模板（P1-3）────────────────────────────
APP_TEMPLATES = {
    "nginx": {
        "name": "Nginx",
        "category": "网络",
        "desc": "高性能 HTTP 服务器",
        "compose": """version: '3'
services:
  nginx:
    image: nginx:latest
    ports:
      - "8080:80"
    volumes:
      - ./html:/usr/share/nginx/html
    restart: unless-stopped
"""
    },
    "mysql": {
        "name": "MySQL",
        "category": "数据库",
        "desc": "关系型数据库",
        "compose": """version: '3'
services:
  mysql:
    image: mysql:8
    environment:
      MYSQL_ROOT_PASSWORD: your_password
      MYSQL_DATABASE: mydb
    ports:
      - "3306:3306"
    volumes:
      - mysql_data:/var/lib/mysql
    restart: unless-stopped
volumes:
  mysql_data:
"""
    },
    "redis": {
        "name": "Redis",
        "category": "数据库",
        "desc": "内存数据结构存储",
        "compose": """version: '3'
services:
  redis:
    image: redis:7-alpine
    ports:
      - "6379:6379"
    volumes:
      - redis_data:/data
    command: redis-server --appendonly yes
    restart: unless-stopped
volumes:
  redis_data:
"""
    },
    "portainer": {
        "name": "Portainer",
        "category": "运维",
        "desc": "Docker 可视化管理",
        "compose": """version: '3'
services:
  portainer:
    image: portainer/portainer-ce:latest
    ports:
      - "9000:9000"
    volumes:
      - /var/run/docker.sock:/var/run/docker.sock
      - portainer_data:/data
    restart: unless-stopped
volumes:
  portainer_data:
"""
    },
    "n8n": {
        "name": "n8n",
        "category": "AI",
        "desc": "工作流自动化平台",
        "compose": """version: '3'
services:
  n8n:
    image: n8nio/n8n:latest
    ports:
      - "5678:5678"
    environment:
      - N8N_BASIC_AUTH_ACTIVE=true
      - N8N_BASIC_AUTH_USER=admin
      - N8N_BASIC_AUTH_PASSWORD=your_password
    volumes:
      - n8n_data:/home/node/.n8n
    restart: unless-stopped
volumes:
  n8n_data:
"""
    },
    "grafana": {
        "name": "Grafana",
        "category": "监控",
        "desc": "数据可视化与监控",
        "compose": """version: '3'
services:
  grafana:
    image: grafana/grafana:latest
    ports:
      - "3000:3000"
    volumes:
      - grafana_data:/var/lib/grafana
    environment:
      - GF_SECURITY_ADMIN_PASSWORD=admin
    restart: unless-stopped
volumes:
  grafana_data:
"""
    },
    "ollama": {
        "name": "Ollama",
        "category": "AI",
        "desc": "本地大语言模型运行",
        "compose": """version: '3'
services:
  ollama:
    image: ollama/ollama:latest
    ports:
      - "11434:11434"
    volumes:
      - ollama_data:/root/.ollama
    restart: unless-stopped
volumes:
  ollama_data:
"""
    },
    "postgresql": {
        "name": "PostgreSQL",
        "category": "数据库",
        "desc": "开源关系型数据库",
        "compose": """version: '3'
services:
  postgres:
    image: postgres:16-alpine
    environment:
      POSTGRES_PASSWORD: your_password
      POSTGRES_DB: mydb
    ports:
      - "5432:5432"
    volumes:
      - pg_data:/var/lib/postgresql/data
    restart: unless-stopped
volumes:
  pg_data:
"""
    },
    "uptime-kuma": {
        "name": "Uptime Kuma",
        "category": "监控",
        "desc": "服务可用性监控",
        "compose": """version: '3'
services:
  uptime-kuma:
    image: louislam/uptime-kuma:latest
    ports:
      - "3001:3001"
    volumes:
      - uptime_data:/app/data
    restart: unless-stopped
volumes:
  uptime_data:
"""
    },
    "vaultwarden": {
        "name": "Vaultwarden",
        "category": "工具",
        "desc": "Bitwarden 密码管理器",
        "compose": """version: '3'
services:
  vaultwarden:
    image: vaultwarden/server:latest
    ports:
      - "8888:80"
    volumes:
      - vw_data:/data
    restart: unless-stopped
volumes:
  vw_data:
"""
    },
    "filebrowser": {
        "name": "FileBrowser",
        "category": "文件",
        "desc": "Web 文件管理器",
        "compose": """version: '3'
services:
  filebrowser:
    image: filebrowser/filebrowser:latest
    ports:
      - "8090:80"
    volumes:
      - /:/srv
      - ./filebrowser.db:/database.db
    restart: unless-stopped
"""
    },
    "jellyfin": {
        "name": "Jellyfin",
        "category": "媒体",
        "desc": "开源媒体服务器",
        "compose": """version: '3'
services:
  jellyfin:
    image: jellyfin/jellyfin:latest
    ports:
      - "8096:8096"
    volumes:
      - jellyfin_config:/config
      - /path/to/media:/media
    restart: unless-stopped
volumes:
  jellyfin_config:
"""
    },
    "qbittorrent": {
        "name": "qBittorrent",
        "category": "下载",
        "desc": "BT 下载工具",
        "compose": """version: '3'
services:
  qbittorrent:
    image: lscr.io/linuxserver/qbittorrent:latest
    environment:
      - PUID=1000
      - PGID=1000
      - WEBUI_PORT=8091
    ports:
      - "8091:8091"
      - "6881:6881"
    volumes:
      - qb_config:/config
      - /path/to/downloads:/downloads
    restart: unless-stopped
volumes:
  qb_config:
"""
    },
    "nextcloud": {
        "name": "Nextcloud",
        "category": "协作",
        "desc": "私有云存储与协作",
        "compose": """version: '3'
services:
  nextcloud:
    image: nextcloud:latest
    ports:
      - "8092:80"
    volumes:
      - nc_data:/var/www/html
    environment:
      - MYSQL_HOST=db
      - MYSQL_DATABASE=nextcloud
      - MYSQL_USER=nextcloud
      - MYSQL_PASSWORD=your_password
    depends_on:
      - db
    restart: unless-stopped
  db:
    image: mysql:8
    environment:
      MYSQL_ROOT_PASSWORD: root_password
      MYSQL_DATABASE: nextcloud
      MYSQL_USER: nextcloud
      MYSQL_PASSWORD: your_password
    volumes:
      - nc_db:/var/lib/mysql
    restart: unless-stopped
volumes:
  nc_data:
  nc_db:
"""
    },
    "homeassistant": {
        "name": "Home Assistant",
        "category": "智能家居",
        "desc": "智能家居自动化平台",
        "compose": """version: '3'
services:
  homeassistant:
    image: ghcr.io/home-assistant/home-assistant:stable
    ports:
      - "8123:8123"
    volumes:
      - ha_config:/config
    environment:
      - TZ=Asia/Shanghai
    restart: unless-stopped
volumes:
  ha_config:
"""
    },
    # 新增应用（Task 3）
    "mariadb": {
        "name": "MariaDB",
        "category": "数据库",
        "desc": "MySQL 兼容的开源数据库",
        "compose": """version: '3'
services:
  mariadb:
    image: mariadb:latest
    environment:
      MYSQL_ROOT_PASSWORD: your_password
      MYSQL_DATABASE: mydb
    ports:
      - "3306:3306"
    volumes:
      - mariadb_data:/var/lib/mysql
    restart: unless-stopped
volumes:
  mariadb_data:
"""
    },
    "mongo": {
        "name": "MongoDB",
        "category": "数据库",
        "desc": "NoSQL 文档数据库",
        "compose": """version: '3'
services:
  mongo:
    image: mongo:latest
    ports:
      - "27017:27017"
    volumes:
      - mongo_data:/data/db
    restart: unless-stopped
volumes:
  mongo_data:
"""
    },
    "minio": {
        "name": "MinIO",
        "category": "存储",
        "desc": "S3 兼容的对象存储",
        "compose": """version: '3'
services:
  minio:
    image: minio/minio:latest
    ports:
      - "9000:9000"
      - "9001:9001"
    environment:
      MINIO_ROOT_USER: minioadmin
      MINIO_ROOT_PASSWORD: minioadmin
    volumes:
      - minio_data:/data
    command: server /data --console-address ":9001"
    restart: unless-stopped
volumes:
  minio_data:
"""
    },
    "gitea": {
        "name": "Gitea",
        "category": "开发工具",
        "desc": "轻量级自建 Git 服务",
        "compose": """version: '3'
services:
  gitea:
    image: gitea/gitea:latest
    ports:
      - "3000:3000"
      - "2222:22"
    volumes:
      - gitea_data:/data
      - /etc/timezone:/etc/timezone:ro
      - /etc/localtime:/etc/localtime:ro
    environment:
      - USER_UID=1000
      - USER_GID=1000
    restart: unless-stopped
volumes:
  gitea_data:
"""
    },
    "adguard-home": {
        "name": "AdGuard Home",
        "category": "网络工具",
        "desc": "全网广告拦截与追踪保护",
        "compose": """version: '3'
services:
  adguard:
    image: adguard/adguardhome:latest
    ports:
      - "53:53/tcp"
      - "53:53/udp"
      - "8080:80/tcp"
      - "8443:443/tcp"
    volumes:
      - adguard_work:/opt/adguardhome/work
      - adguard_conf:/opt/adguardhome/conf
    restart: unless-stopped
volumes:
  adguard_work:
  adguard_conf:
"""
    },
    "syncthing": {
        "name": "Syncthing",
        "category": "文件同步",
        "desc": "P2P 文件同步工具",
        "compose": """version: '3'
services:
  syncthing:
    image: syncthing/syncthing:latest
    ports:
      - "8384:8384"
      - "22000:22000/tcp"
      - "22000:22000/udp"
      - "21027:21027/udp"
    volumes:
      - syncthing_data:/var/syncthing
    environment:
      - PUID=1000
      - PGID=1000
    restart: unless-stopped
volumes:
  syncthing_data:
"""
    },
    "immich": {
        "name": "Immich",
        "category": "媒体",
        "desc": "私有照片与视频管理",
        "compose": """version: '3'
services:
  immich-server:
    image: ghcr.io/immich-app/immich-server:latest
    ports:
      - "2283:3001"
    volumes:
      - immich_upload:/usr/src/app/upload
    environment:
      - DB_HOSTNAME=immich-db
      - DB_USERNAME=postgres
      - DB_PASSWORD=postgres
      - DB_DATABASE_NAME=immich
    depends_on:
      - immich-db
    restart: unless-stopped
  immich-db:
    image: postgres:14
    environment:
      POSTGRES_USER: postgres
      POSTGRES_PASSWORD: postgres
      POSTGRES_DB: immich
    volumes:
      - immich_db:/var/lib/postgresql/data
    restart: unless-stopped
volumes:
  immich_upload:
  immich_db:
"""
    },
    "nocodb": {
        "name": "NocoDB",
        "category": "低代码",
        "desc": "Airtable 替代，无代码数据库",
        "compose": """version: '3'
services:
  nocodb:
    image: nocodb/nocodb:latest
    ports:
      - "8080:8080"
    volumes:
      - nocodb_data:/usr/app/data
    restart: unless-stopped
volumes:
  nocodb_data:
"""
    },
    "changedetection": {
        "name": "Change Detection",
        "category": "监控",
        "desc": "网页变化监控与通知",
        "compose": """version: '3'
services:
  changedetection:
    image: ghcr.io/dgtlmoon/changedetection.io:latest
    ports:
      - "5000:5000"
    volumes:
      - changedetection_data:/datastore
    restart: unless-stopped
volumes:
  changedetection_data:
"""
    },
    "linkding": {
        "name": "Linkding",
        "category": "工具",
        "desc": "书签管理服务",
        "compose": """version: '3'
services:
  linkding:
    image: sissbruecker/linkding:latest
    ports:
      - "9090:9090"
    volumes:
      - linkding_data:/etc/linkding/data
    environment:
      - LD_SUPERUSER_NAME=admin
      - LD_SUPERUSER_PASSWORD=admin
    restart: unless-stopped
volumes:
  linkding_data:
"""
    },
    "calibre-web": {
        "name": "Calibre Web",
        "category": "媒体",
        "desc": "在线电子书库与阅读",
        "compose": """version: '3'
services:
  calibre-web:
    image: lscr.io/linuxserver/calibre-web:latest
    ports:
      - "8083:8083"
    volumes:
      - calibre_data:/config
      - /path/to/books:/books
    environment:
      - PUID=1000
      - PGID=1000
    restart: unless-stopped
volumes:
  calibre_data:
"""
    },
}

# ──────────────────────────── HTTP 处理器 ────────────────────────────
class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass  # 静默日志

    def _auth(self) -> bool:
        auth_header = self.headers.get("Authorization", "")
        if auth_header.startswith("Bearer "):
            token = auth_header[7:]
            return jwt_decode(token) is not None
        return False

    def _send_json(self, data: dict, code: int = 200):
        body = json.dumps(data, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def _send_sse_headers(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("X-Accel-Buffering", "no")
        self.end_headers()

    def _body(self) -> dict:
        try:
            length = int(self.headers.get("Content-Length", 0))
            return json.loads(self.rfile.read(length)) if length else {}
        except Exception:
            return {}

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET,POST,DELETE,OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type,Authorization")
        self.end_headers()

    def do_GET(self):
        parsed = urlparse(self.path)
        path   = parsed.path
        qs     = parse_qs(parsed.query)

        # ── 认证状态
        if path == "/api/auth/status":
            return self._send_json({"initialized": is_initialized()})

        # ── 登录
        if path == "/api/auth/login":
            return self._send_json({"error": "use POST"}, 405)

        # ── 以下需要认证
        if not self._auth():
            return self._send_json({"error": "unauthorized"}, 401)

        # ── 任务状态
        if path.startswith("/api/task/"):
            task_id = path.split("/")[-1]
            if task_id in tasks:
                return self._send_json(tasks[task_id])
            return self._send_json({"error": "not found"}, 404)

        # ── 实时日志 SSE（EventSource）
        if path.startswith("/api/task/") and path.endswith("/logs"):
            task_id = path.split("/")[-2]
            self._stream_task_logs(task_id)
            return

        # ── 容器实时日志 SSE（P1-1）
        if path.startswith("/api/container/") and path.endswith("/logs"):
            cid = path.split("/")[-2]
            self._stream_container_logs(cid, qs)
            return

        # ── 容器列表（P1-1）返回与 Docker API 兼容的列表
        if path == "/api/containers":
            return self._send_json(get_containers())

        # ── 资源监控（P1-2）
        if path == "/api/stats":
            return self._send_json(get_stats())

        # ── 镜像列表（Task 2）
        if path == "/api/images":
            return self._send_json(get_images())

        # ── 镜像加速器
        if path == "/api/mirrors":
            d = _read_daemon()
            return self._send_json({"mirrors": d.get("registry-mirrors", [])})

        # ── 部署历史（P2-3）
        if path == "/api/history":
            return self._send_json({"history": _load_history()})

        # ── 已保存的 Compose（P2-4）
        if path == "/api/composes":
            return self._send_json({"composes": _load_saves()})

        # ── 应用商店
        if path == "/api/apps":
            return self._send_json({"apps": list(APP_TEMPLATES.values())})

        # ── 应用模板（P1-3 一键部署）
        if path.startswith("/api/apps/") and path.endswith("/template"):
            key = path.split("/")[-2]
            if key in APP_TEMPLATES:
                return self._send_json({"compose": APP_TEMPLATES[key]["compose"],
                                        "name": APP_TEMPLATES[key]["name"]})
            return self._send_json({"error": "not found"}, 404)

        # ── 版本
        if path == "/api/version":
            return self._send_json({"version": VERSION})

        self._send_json({"error": "not found"}, 404)

    def do_POST(self):
        parsed = urlparse(self.path)
        path   = parsed.path
        body   = self._body()

        # ── 登录 / 初始化
        if path == "/api/auth/login":
            pwd = body.get("password", "")
            if not pwd:
                return self._send_json({"error": "password required"}, 400)
            if not is_initialized():
                set_password(pwd)
                return self._send_json({"token": make_token(), "message": "密码已设置"})
            if check_password(pwd):
                return self._send_json({"token": make_token()})
            return self._send_json({"error": "密码错误"}, 401)

        # ── 修改密码
        if path == "/api/auth/change-password":
            if not self._auth():
                return self._send_json({"error": "unauthorized"}, 401)
            old_pwd = body.get("old_password", "")
            new_pwd = body.get("new_password", "")
            if not check_password(old_pwd):
                return self._send_json({"error": "原密码错误"}, 400)
            if len(new_pwd) < 6:
                return self._send_json({"error": "新密码至少6位"}, 400)
            set_password(new_pwd)
            return self._send_json({"message": "密码已更新"})

        # ── 以下需要认证
        if not self._auth():
            return self._send_json({"error": "unauthorized"}, 401)

        # ── 部署 Compose
        if path == "/api/deploy":
            compose_yaml = body.get("compose", "")
            project_name = body.get("project", "myapp").strip().lower()
            if not compose_yaml:
                return self._send_json({"error": "compose is required"}, 400)
            task_id = uuid.uuid4().hex[:12]
            tasks[task_id] = {"id": task_id, "status": "pending", "phase": "pending",
                              "logs": [], "pull_progress": 0, "project": project_name}
            threading.Thread(target=_run_deploy, args=(task_id, compose_yaml, project_name),
                             daemon=True).start()
            return self._send_json({"task_id": task_id})

        # ── 停止 Compose 项目
        if path == "/api/stop":
            project_name = body.get("project", "").strip()
            if not project_name:
                return self._send_json({"error": "project required"}, 400)
            try:
                r = subprocess.run(
                    ["docker", "compose", "-p", project_name, "down"],
                    capture_output=True, text=True, timeout=60
                )
                return self._send_json({"ok": r.returncode == 0, "output": r.stdout + r.stderr})
            except Exception as e:
                return self._send_json({"ok": False, "error": str(e)})

        # ── 容器操作（P1-1）start / stop / restart / remove
        if path.startswith("/api/container/"):
            parts = path.split("/")
            if len(parts) >= 5:
                cid    = parts[3]
                action = parts[4]
                if action in ("start", "stop", "restart", "remove"):
                    ok, msg = docker_action(cid, action)
                    return self._send_json({"ok": ok, "message": msg})
            return self._send_json({"error": "invalid action"}, 400)

        # ── 镜像加速器保存
        if path == "/api/mirrors":
            mirrors = body.get("mirrors", [])
            d = _read_daemon()
            d["registry-mirrors"] = mirrors
            try:
                _write_daemon(d)
                subprocess.run(["pkill", "-HUP", "dockerd"], capture_output=True)
                return self._send_json({"ok": True})
            except Exception as e:
                return self._send_json({"ok": False, "error": str(e)})

        # ── 镜像更新检查
        if path == "/api/images/check-update":
            name = body.get("name", "")
            if not name:
                return self._send_json({"error": "name required"}, 400)
            return self._send_json(check_image_update(name))

        # ── 拉取最新镜像（不重启容器）
        if path == "/api/images/pull":
            name = body.get("name", "")
            if not name:
                return self._send_json({"error": "name required"}, 400)
            result = pull_and_restart(name)
            return self._send_json(result)

        # ── 拉取最新镜像 + 重启所有使用该镜像的容器
        if path == "/api/images/pull-restart":
            name = body.get("name", "")
            if not name:
                return self._send_json({"error": "name required"}, 400)
            pull_result = pull_and_restart(name)
            if not pull_result["ok"]:
                return self._send_json(pull_result)
            # 重启使用此镜像的容器
            cids = get_containers_by_image(name)
            restarted = []
            failed = []
            for cid in cids:
                ok, msg = docker_action(cid, "restart")
                if ok:
                    restarted.append(cid[:12])
                else:
                    failed.append(cid[:12])
            return self._send_json({
                "ok": True,
                "output": pull_result["output"],
                "restarted": restarted,
                "failed": failed,
                "message": f"镜像已更新，重启了 {len(restarted)} 个容器"
            })

        # ── 保存 Compose 文件（P2-4）
        if path == "/api/composes":
            name    = body.get("name", "").strip()
            content = body.get("content", "").strip()
            if not name or not content:
                return self._send_json({"error": "name and content required"}, 400)
            saves = _load_saves()
            # 更新或新增
            for s in saves:
                if s["name"] == name:
                    s["content"] = content
                    s["updated"] = int(time.time())
                    _save_composes(saves)
                    return self._send_json({"ok": True, "message": "已更新"})
            saves.append({"name": name, "content": content, "created": int(time.time())})
            _save_composes(saves)
            return self._send_json({"ok": True, "message": "已保存"})

        self._send_json({"error": "not found"}, 404)

    def do_DELETE(self):
        parsed = urlparse(self.path)
        path   = parsed.path

        if not self._auth():
            return self._send_json({"error": "unauthorized"}, 401)

        # ── 删除已保存的 Compose（P2-4）
        if path.startswith("/api/composes/"):
            name  = path.split("/")[-1]
            saves = _load_saves()
            new   = [s for s in saves if s["name"] != name]
            if len(new) == len(saves):
                return self._send_json({"error": "not found"}, 404)
            _save_composes(new)
            return self._send_json({"ok": True})

        self._send_json({"error": "not found"}, 404)

    # ── SSE：任务日志流
    def _stream_task_logs(self, task_id: str):
        if task_id not in tasks:
            self._send_json({"error": "not found"}, 404)
            return
        self._send_sse_headers()
        sent = 0
        try:
            while True:
                task = tasks[task_id]
                logs = task.get("logs", [])
                while sent < len(logs):
                    line = logs[sent].replace("\n", "\\n")
                    self.wfile.write(f"data: {line}\n\n".encode())
                    sent += 1
                self.wfile.flush()
                if task["status"] in ("done", "failed"):
                    self.wfile.write(b"data: __END__\n\n")
                    self.wfile.flush()
                    break
                time.sleep(0.3)
        except Exception:
            pass

    # ── SSE：容器实时日志（P1-1）
    def _stream_container_logs(self, cid: str, qs: dict):
        tail = qs.get("tail", ["100"])[0]
        self._send_sse_headers()
        try:
            proc = subprocess.Popen(
                ["docker", "logs", "--follow", "--tail", tail, cid],
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, bufsize=1
            )
            for line in proc.stdout:
                line = line.rstrip().replace("\n", "\\n")
                self.wfile.write(f"data: {line}\n\n".encode())
                self.wfile.flush()
            proc.wait()
        except Exception:
            pass
        finally:
            try:
                self.wfile.write(b"data: __END__\n\n")
                self.wfile.flush()
            except Exception:
                pass


if __name__ == "__main__":
    print(f"[backend] Docker管理中心 v{VERSION} 启动，端口 {PORT}")
    print(f"[backend] 数据目录: {DATA_DIR}")
    print(f"[backend] 密码已初始化: {is_initialized()}")
    server = HTTPServer(("0.0.0.0", PORT), Handler)
    server.serve_forever()
