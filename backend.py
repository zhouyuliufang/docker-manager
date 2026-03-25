#!/usr/bin/env python3
"""
Docker 管理中心 - 后端服务
版本：2.2.0  作者：冷锋
"""
import os
import json
import subprocess
import tempfile
import shutil
import threading
import time
import re
import hmac
import hashlib
import base64
import secrets
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs

# ── 数据存储路径 ──────────────────────────────
DATA_DIR = os.environ.get('DATA_DIR', '/data')
CONFIG_FILE = os.path.join(DATA_DIR, 'config.json')
JWT_SECRET = None

os.makedirs(DATA_DIR, exist_ok=True)

# ── JWT 实现（纯标准库） ───────────────────────
def load_or_create_secret():
    global JWT_SECRET
    secret_file = os.path.join(DATA_DIR, '.jwt_secret')
    if os.path.exists(secret_file):
        with open(secret_file, 'r') as f:
            JWT_SECRET = f.read().strip()
    else:
        JWT_SECRET = secrets.token_hex(32)
        with open(secret_file, 'w') as f:
            f.write(JWT_SECRET)

def b64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b'=').decode()

def b64url_decode(s: str) -> bytes:
    pad = 4 - len(s) % 4
    if pad != 4:
        s += '=' * pad
    return base64.urlsafe_b64decode(s)

def create_token(expire_hours: int = 24 * 7) -> str:
    header = b64url_encode(json.dumps({'alg': 'HS256', 'typ': 'JWT'}).encode())
    exp = int(time.time()) + expire_hours * 3600
    payload = b64url_encode(json.dumps({'sub': 'admin', 'exp': exp, 'iat': int(time.time())}).encode())
    sig_input = f'{header}.{payload}'.encode()
    sig = hmac.new(JWT_SECRET.encode(), sig_input, hashlib.sha256).digest()
    return f'{header}.{payload}.{b64url_encode(sig)}'

def verify_token(token: str):
    """验证 token，成功返回 True，失败返回 False"""
    try:
        parts = token.split('.')
        if len(parts) != 3:
            return False
        header, payload, sig = parts
        sig_input = f'{header}.{payload}'.encode()
        expected_sig = hmac.new(JWT_SECRET.encode(), sig_input, hashlib.sha256).digest()
        if not hmac.compare_digest(b64url_encode(expected_sig), sig):
            return False
        data = json.loads(b64url_decode(payload))
        if data.get('exp', 0) < time.time():
            return False
        return True
    except Exception:
        return False

# ── 密码管理（单用户，无用户名）──────────────────
config_lock = threading.Lock()

def hash_password(password: str) -> str:
    salt = secrets.token_hex(16)
    h = hashlib.pbkdf2_hmac('sha256', password.encode(), salt.encode(), 100000)
    return f'{salt}${h.hex()}'

def check_password(password: str, hashed: str) -> bool:
    try:
        salt, h = hashed.split('$', 1)
        expected = hashlib.pbkdf2_hmac('sha256', password.encode(), salt.encode(), 100000)
        return hmac.compare_digest(h, expected.hex())
    except Exception:
        return False

def load_config() -> dict:
    with config_lock:
        if not os.path.exists(CONFIG_FILE):
            return {}
        with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)

def save_config(cfg: dict):
    with config_lock:
        with open(CONFIG_FILE, 'w', encoding='utf-8') as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)

def is_initialized() -> bool:
    """是否已设置过密码"""
    cfg = load_config()
    return bool(cfg.get('password_hash'))

# ── 任务存储 ──────────────────────────────────
deploy_tasks = {}
task_lock = threading.Lock()

def gen_task_id():
    import random, string
    return ''.join(random.choices(string.ascii_lowercase + string.digits, k=8))

# ── HTTP 处理器 ──────────────────────────────
class Handler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        pass

    def send_json(self, code, data):
        body = json.dumps(data, ensure_ascii=False).encode('utf-8')
        self.send_response(code)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', len(body))
        self._cors_headers()
        self.end_headers()
        self.wfile.write(body)

    def _cors_headers(self):
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET, POST, DELETE, PUT, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type, Authorization')

    def do_OPTIONS(self):
        self.send_response(204)
        self._cors_headers()
        self.end_headers()

    def read_body(self):
        length = int(self.headers.get('Content-Length', 0))
        return self.rfile.read(length).decode('utf-8') if length else ''

    def get_token(self):
        auth = self.headers.get('Authorization', '')
        if auth.startswith('Bearer '):
            return auth[7:]
        return None

    def require_auth(self):
        """验证 token，成功返回 True，否则发 401 并返回 False"""
        token = self.get_token()
        if not token:
            self.send_json(401, {'error': '未登录，请先登录'})
            return False
        if not verify_token(token):
            self.send_json(401, {'error': 'Token 已过期，请重新登录'})
            return False
        return True

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path
        qs = parse_qs(parsed.query)

        # 健康检查（无需认证）
        if path == '/api/health':
            self.send_json(200, {'status': 'ok', 'time': time.time(), 'initialized': is_initialized()})
            return

        # 系统初始化状态（无需认证）
        if path == '/api/auth/status':
            self.send_json(200, {'initialized': is_initialized()})
            return

        # ── 以下需要认证 ──
        if not self.require_auth():
            return

        if path == '/api/compose/status':
            task_id = qs.get('id', [''])[0]
            with task_lock:
                task = deploy_tasks.get(task_id)
            if task:
                self.send_json(200, task)
            else:
                self.send_json(404, {'error': '任务不存在'})

        elif path == '/api/compose/list':
            try:
                result = subprocess.run(
                    ['docker', 'compose', 'ls', '--format', 'json'],
                    capture_output=True, text=True, timeout=10
                )
                try:
                    data = json.loads(result.stdout) if result.stdout.strip() else []
                except:
                    data = []
                self.send_json(200, {'projects': data})
            except Exception as e:
                self.send_json(500, {'error': str(e)})

        elif path == '/api/mirrors':
            # 读取当前镜像加速器列表
            mirrors, err = _read_daemon_mirrors()
            if err:
                self.send_json(500, {'error': err})
            else:
                self.send_json(200, {'mirrors': mirrors})

        else:
            self.send_json(404, {'error': 'not found'})

    def do_POST(self):
        parsed = urlparse(self.path)
        path = parsed.path
        body_raw = self.read_body()

        try:
            body = json.loads(body_raw) if body_raw else {}
        except:
            self.send_json(400, {'error': '请求体格式错误'})
            return

        # ── 登录 / 首次设置密码 ──
        if path == '/api/auth/login':
            password = body.get('password', '')
            if not password:
                self.send_json(400, {'error': '密码不能为空'})
                return

            if not is_initialized():
                # 首次登录：设置密码
                if len(password) < 6:
                    self.send_json(400, {'error': '密码长度不能少于 6 位'})
                    return
                cfg = load_config()
                cfg['password_hash'] = hash_password(password)
                cfg['created_at'] = time.time()
                save_config(cfg)
                token = create_token()
                self.send_json(200, {'message': '密码设置成功，已自动登录', 'token': token, 'first_setup': True})
            else:
                # 正常登录
                cfg = load_config()
                if not check_password(password, cfg.get('password_hash', '')):
                    self.send_json(401, {'error': '密码错误'})
                    return
                token = create_token()
                self.send_json(200, {'message': '登录成功', 'token': token, 'first_setup': False})
            return

        # ── 以下需要认证 ──
        if not self.require_auth():
            return

        # 修改密码
        if path == '/api/auth/change-password':
            old_pw = body.get('old_password', '')
            new_pw = body.get('new_password', '')
            if len(new_pw) < 6:
                self.send_json(400, {'error': '新密码长度不能少于 6 位'})
                return
            cfg = load_config()
            if not check_password(old_pw, cfg.get('password_hash', '')):
                self.send_json(401, {'error': '当前密码错误'})
                return
            cfg['password_hash'] = hash_password(new_pw)
            save_config(cfg)
            self.send_json(200, {'message': '密码已修改'})
            return

        # 部署 Compose（异步）
        if path == '/api/compose/deploy':
            compose_content = body.get('compose', '')
            project_name = body.get('project', '').strip()
            pull_first = body.get('pull', True)

            if not compose_content:
                self.send_json(400, {'error': 'compose 内容不能为空'})
                return

            if project_name and not re.match(r'^[a-zA-Z0-9_-]+$', project_name):
                self.send_json(400, {'error': '项目名只能包含字母、数字、下划线和连字符'})
                return

            task_id = gen_task_id()
            with task_lock:
                deploy_tasks[task_id] = {
                    'id': task_id,
                    'status': 'running',
                    'logs': [],
                    'started_at': time.time(),
                    'pull_progress': 0,
                    'phase': 'pull' if pull_first else 'deploy',
                }

            t = threading.Thread(
                target=run_compose_deploy,
                args=(task_id, compose_content, project_name, pull_first),
                daemon=True
            )
            t.start()
            self.send_json(202, {'task_id': task_id, 'message': '部署任务已启动'})

        # 停止/删除 compose 项目
        elif path == '/api/compose/down':
            project_name = body.get('project', '').strip()
            remove_volumes = body.get('volumes', False)

            if not project_name:
                self.send_json(400, {'error': '项目名不能为空'})
                return

            task_id = gen_task_id()
            with task_lock:
                deploy_tasks[task_id] = {
                    'id': task_id,
                    'status': 'running',
                    'logs': [],
                    'started_at': time.time(),
                }

            t = threading.Thread(
                target=run_compose_down,
                args=(task_id, project_name, remove_volumes),
                daemon=True
            )
            t.start()
            self.send_json(202, {'task_id': task_id, 'message': '停止任务已启动'})

        # 验证 compose 格式
        elif path == '/api/compose/validate':
            compose_content = body.get('compose', '')
            if not compose_content:
                self.send_json(400, {'error': 'compose 内容不能为空'})
                return

            tmpdir = tempfile.mkdtemp()
            try:
                compose_path = os.path.join(tmpdir, 'docker-compose.yml')
                with open(compose_path, 'w', encoding='utf-8') as f:
                    f.write(compose_content)

                result = subprocess.run(
                    ['docker', 'compose', '-f', compose_path, 'config'],
                    capture_output=True, text=True, timeout=15
                )
                if result.returncode == 0:
                    self.send_json(200, {'valid': True, 'message': '格式正确'})
                else:
                    self.send_json(200, {'valid': False, 'error': result.stderr.strip()})
            except Exception as e:
                self.send_json(500, {'error': str(e)})
            finally:
                shutil.rmtree(tmpdir, ignore_errors=True)

        # 更新镜像加速器列表
        elif path == '/api/mirrors':
            mirrors = body.get('mirrors', [])
            if not isinstance(mirrors, list):
                self.send_json(400, {'error': 'mirrors 必须是数组'})
                return
            # 验证每项是合法 URL
            for m in mirrors:
                if not isinstance(m, str) or not m.startswith('http'):
                    self.send_json(400, {'error': f'非法地址: {m}'})
                    return
            err = _write_daemon_mirrors(mirrors)
            if err:
                self.send_json(500, {'error': err})
            else:
                self.send_json(200, {'message': '保存成功！如镜像拉取仍使用旧配置，请在 NAS 系统设置中重启 Docker 服务使其完全生效。'})

        else:
            self.send_json(404, {'error': 'not found'})


def _parse_pull_progress(line: str, layer_state: dict) -> dict | None:
    """
    解析 docker compose pull 的输出行，更新 layer_state，返回进度信息或 None。
    layer_state 结构：{ layer_id: {'status': str, 'current': int, 'total': int} }

    Docker pull 输出典型格式（--progress=plain 模式）：
      #1 [internal] load metadata
      #2 pulling sha256:...
      #2 DONE 1.2s
      
    或旧式 JSON 流（docker pull image --quiet）：
      {"status":"Pulling from ...","id":"xxx"}
      {"status":"Downloading","progressDetail":{"current":1024,"total":2048},"id":"abc123"}
      {"status":"Pull complete","id":"abc123"}
    """
    # 尝试解析 JSON 格式（docker pull 直接输出）
    line = line.strip()
    if not line:
        return None

    try:
        obj = json.loads(line)
        layer_id = obj.get('id', '')
        status = obj.get('status', '')
        detail = obj.get('progressDetail', {})

        if not layer_id:
            return None

        if layer_id not in layer_state:
            layer_state[layer_id] = {'status': '', 'current': 0, 'total': 0}

        layer_state[layer_id]['status'] = status

        if detail:
            cur = detail.get('current', 0)
            tot = detail.get('total', 0)
            if tot > 0:
                layer_state[layer_id]['current'] = cur
                layer_state[layer_id]['total'] = tot
            elif status in ('Download complete', 'Pull complete', 'Already exists', 'Layer already exists'):
                layer_state[layer_id]['current'] = layer_state[layer_id].get('total', 1) or 1
                layer_state[layer_id]['total'] = layer_state[layer_id].get('total', 1) or 1

        if status in ('Download complete', 'Pull complete', 'Already exists', 'Layer already exists'):
            t = layer_state[layer_id]['total'] or 1
            layer_state[layer_id]['current'] = t
            layer_state[layer_id]['total'] = t

        # 计算总体百分比（只计算有 total 的 layer）
        layers_with_total = [v for v in layer_state.values() if v['total'] > 0]
        if layers_with_total:
            total_bytes = sum(v['total'] for v in layers_with_total)
            done_bytes = sum(v['current'] for v in layers_with_total)
            pct = int(done_bytes * 100 / total_bytes) if total_bytes > 0 else 0
        else:
            # 没有 total 信息时，用完成 layer 数估算
            all_layers = [v for v in layer_state.values() if v['status']]
            done_layers = [v for v in all_layers if v['status'] in
                           ('Download complete', 'Pull complete', 'Already exists', 'Layer already exists')]
            pct = int(len(done_layers) * 100 / len(all_layers)) if all_layers else 0

        return {'pct': pct, 'layer_id': layer_id, 'status': status}

    except (json.JSONDecodeError, Exception):
        pass

    # 纯文本格式解析（docker compose pull --progress=plain）
    # 格式：#N [service/image] status
    m = re.match(r'^#\d+\s+(.+)$', line)
    if m:
        return {'pct': -1, 'text': m.group(1)}  # -1 表示纯文本，无法计算百分比

    return None


# ── 镜像加速器管理 ─────────────────────────────
daemon_lock = threading.Lock()
_DAEMON_JSON_CANDIDATES = [
    '/etc/docker/daemon.json',
    '/var/lib/docker/daemon.json',
]

def _get_daemon_json_path() -> str:
    """
    返回 daemon.json 的实际路径。
    优先返回已存在的文件路径；若都不存在，尝试通过 docker info 获取，
    最后 fallback 到 /etc/docker/daemon.json（会自动创建目录）。
    """
    for p in _DAEMON_JSON_CANDIDATES:
        if os.path.exists(p):
            return p
    # 通过 docker info 获取 DockerRootDir，再拼路径
    try:
        r = subprocess.run(['docker', 'info', '--format', '{{.DockerRootDir}}'],
                           capture_output=True, text=True, timeout=5)
        root = r.stdout.strip()
        if root:
            p = os.path.join(os.path.dirname(root), 'daemon.json')
            if os.path.exists(p):
                return p
    except Exception:
        pass
    return _DAEMON_JSON_CANDIDATES[0]  # fallback

def _read_daemon_mirrors() -> tuple:
    """读取 daemon.json 中的 registry-mirrors，返回 (列表, 错误信息)"""
    with daemon_lock:
        try:
            path = _get_daemon_json_path()
            if not os.path.exists(path):
                return [], ''
            with open(path, 'r', encoding='utf-8') as f:
                cfg = json.load(f)
            return cfg.get('registry-mirrors', []), ''
        except Exception as e:
            return [], f'读取 daemon.json 失败: {e}'

def _write_daemon_mirrors(mirrors: list) -> str:
    """
    将 registry-mirrors 写入 daemon.json（保留其他字段），
    然后发送 SIGHUP 给 dockerd 使其重载配置。
    返回空字符串表示成功，否则返回错误信息。
    """
    with daemon_lock:
        try:
            path = _get_daemon_json_path()
            if os.path.exists(path):
                with open(path, 'r', encoding='utf-8') as f:
                    cfg = json.load(f)
            else:
                cfg = {}

            # 去掉末尾斜杠并去重
            cleaned = []
            seen = set()
            for m in mirrors:
                m = m.strip().rstrip('/')
                if m and m not in seen:
                    seen.add(m)
                    cleaned.append(m)
            cfg['registry-mirrors'] = cleaned

            # 确保目录存在，原子写入
            os.makedirs(os.path.dirname(path), exist_ok=True)
            tmp_path = path + '.tmp'
            with open(tmp_path, 'w', encoding='utf-8') as f:
                json.dump(cfg, f, ensure_ascii=False, indent=2)
            os.replace(tmp_path, path)

        except Exception as e:
            return f'写入 daemon.json 失败: {e}'

    # 通知 dockerd 重载配置（SIGHUP）
    try:
        subprocess.run(
            ['sh', '-c',
             'kill -HUP $(cat /var/run/docker.pid 2>/dev/null) 2>/dev/null'
             ' || pkill -HUP dockerd 2>/dev/null || true'],
            timeout=5, capture_output=True
        )
    except Exception:
        pass
    return ''


def _supports_progress_flag() -> bool:
    """
    检测当前 docker compose 是否支持 --progress 参数。
    通过运行 `docker compose pull --help` 并检查输出中是否包含 '--progress'。
    结果缓存，避免重复检测。
    """
    if not hasattr(_supports_progress_flag, '_cache'):
        try:
            r = subprocess.run(
                ['docker', 'compose', 'pull', '--help'],
                capture_output=True, text=True, timeout=5
            )
            _supports_progress_flag._cache = '--progress' in r.stdout or '--progress' in r.stderr
        except Exception:
            _supports_progress_flag._cache = False
    return _supports_progress_flag._cache


def _run_pull_with_progress(task_id, cmd_base, append_log, set_progress):
    """
    执行 docker compose pull，实时流式解析进度，写入日志和进度。
    自动探测是否支持 --progress=plain，不支持则直接 pull 并记录输出。
    """
    layer_state = {}
    last_pct = -1

    # 根据版本选择是否传 --progress=plain
    if _supports_progress_flag():
        pull_cmd = cmd_base + ['pull', '--progress=plain']
    else:
        pull_cmd = cmd_base + ['pull']

    try:
        proc = subprocess.Popen(
            pull_cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            errors='replace'
        )

        # plain 模式下解析进度
        # 格式示例：
        #   #1 [service] pulling image:tag
        #   #1 pulling from library/nginx
        #   #1 digest: sha256:...
        #   #1 status: image is up to date for nginx:latest
        #   #1 DONE 0.3s
        #
        # 有些版本会输出：
        #   Pulling service ... 100% ████████ 10.2MB/10.2MB
        
        image_steps = {}    # step_num -> {'service': str, 'done': bool}
        total_steps = 0
        done_steps = 0
        pull_log_lines = []   # 收集所有输出行，供错误摘要提取

        deadline = time.time() + 360
        for raw_line in proc.stdout:
            if time.time() > deadline:
                proc.kill()
                append_log('❌ 拉取超时（6分钟）', 'error')
                return False, []

            line = raw_line.rstrip()
            if not line:
                continue

            pull_log_lines.append(line)

            # 解析 buildkit/compose plain 格式
            # "#N pulling from ..." → 新的 layer 步骤
            m_start = re.match(r'^#(\d+)\s+\[([^\]]+)\]\s+pulling\s+(.+)', line, re.IGNORECASE)
            if m_start:
                step = m_start.group(1)
                service = m_start.group(2)
                image = m_start.group(3)
                image_steps[step] = {'service': service, 'image': image, 'done': False}
                total_steps = len(image_steps)
                append_log(f'  📦 {service}: 开始拉取 {image}', 'info')
                continue

            # "#N DONE Xs" → 完成
            m_done = re.match(r'^#(\d+)\s+DONE\s+[\d.]+s', line)
            if m_done:
                step = m_done.group(1)
                if step in image_steps and not image_steps[step]['done']:
                    image_steps[step]['done'] = True
                    done_steps = sum(1 for v in image_steps.values() if v['done'])
                    svc = image_steps[step].get('service', step)
                    append_log(f'  ✅ {svc}: 拉取完成', 'success')
                total_steps = len(image_steps)
                if total_steps > 0:
                    pct = int(done_steps * 100 / total_steps)
                    if pct != last_pct:
                        last_pct = pct
                        set_progress(pct)
                continue

            # "#N [service] status: already ..." → Already exists
            m_exists = re.match(r'^#(\d+)\s+\[([^\]]+)\]\s+status:\s*(.*already.*)', line, re.IGNORECASE)
            if m_exists:
                step = m_exists.group(1)
                service = m_exists.group(2)
                if step not in image_steps:
                    image_steps[step] = {'service': service, 'done': True}
                else:
                    image_steps[step]['done'] = True
                done_steps = sum(1 for v in image_steps.values() if v['done'])
                total_steps = len(image_steps)
                append_log(f'  ✅ {service}: 镜像已是最新', 'info')
                if total_steps > 0:
                    pct = int(done_steps * 100 / total_steps)
                    if pct != last_pct:
                        last_pct = pct
                        set_progress(pct)
                continue

            # 尝试解析百分比格式（某些版本）：
            # "nginx Pulling 100% [=====] 54.1MB/54.1MB"
            m_pct = re.search(r'(\d+)%', line)
            if m_pct and ('pulling' in line.lower() or 'downloading' in line.lower() or 'extracting' in line.lower()):
                pct = min(int(m_pct.group(1)), 99)  # pull 完成前最多99%
                if pct != last_pct and pct > last_pct:
                    last_pct = pct
                    set_progress(pct)
                if line.strip():
                    append_log('  ' + line, 'info')
                continue

            # 过滤掉噪音行（buildkit 内部步骤）
            skip_patterns = [
                r'^#\d+\s+\[internal\]',
                r'^#\d+\s+resolve\s+',
                r'^#\d+\s+sha256:',
                r'^#\d+\s+\d+\.\d+\s+(kB|MB|GB)',
            ]
            if any(re.match(p, line) for p in skip_patterns):
                continue

            # 其余行输出到日志
            if line.strip() and not line.startswith('#'):
                level = 'error' if 'error' in line.lower() else 'info'
                append_log('  ' + line, level)

        proc.wait()

        # 完成后进度设为 100%
        set_progress(100)
        pull_errors = _extract_error_summary(pull_log_lines) if proc.returncode != 0 else []
        return proc.returncode == 0, pull_errors

    except Exception as e:
        append_log(f'❌ 拉取异常: {e}', 'error')
        return False, [str(e)]


def _extract_error_summary(lines: list[str]) -> list[str]:
    """
    从 docker compose 输出中提取最有价值的错误摘要（最多5条）。
    优先级：Error/failed > warning > 其他
    对于常见网络错误，附加解决建议。
    """
    # 高优先级错误关键词
    high_priority = [
        'error', 'failed', 'no such', 'not found', 'permission denied',
        'cannot', 'unable', 'invalid', 'conflict', 'already in use',
        'port is already', 'bind:', 'oci runtime', 'exec format',
        'pull access denied', 'manifest unknown', 'unauthorized',
        'name is already', 'network', 'exit code',
    ]

    # 网络超时/连接失败的识别词 → 给出解决建议
    network_hints = [
        'context deadline exceeded',
        'client.timeout',
        'connection refused',
        'dial tcp',
        'no route to host',
        'network is unreachable',
        'registry-1.docker.io',
        'i/o timeout',
    ]

    errors = []
    has_network_error = False

    for line in lines:
        ll = line.lower().strip()
        if not ll:
            continue
        # 过滤掉进度类噪音行
        if ll.startswith('#') or 'downloading' in ll or 'extracting' in ll:
            continue
        if any(kw in ll for kw in high_priority):
            # 去掉 ANSI 颜色码
            clean = re.sub(r'\x1b\[[0-9;]*m', '', line).strip()
            if clean and clean not in errors:
                errors.append(clean)
            # 检测是否是网络超时错误
            if any(nh in ll for nh in network_hints):
                has_network_error = True
        if len(errors) >= 5:
            break

    # 网络错误时追加解决建议
    if has_network_error:
        errors.append('💡 解决方案：NAS 无法连接 Docker Hub，请在 NAS 的 Docker 设置中配置镜像加速地址（如：https://mirror.iscas.ac.cn 或其他国内镜像源）')

    return errors


def run_compose_deploy(task_id, compose_content, project_name, pull_first):
    tmpdir = tempfile.mkdtemp(prefix='docker_mgr_')
    try:
        compose_path = os.path.join(tmpdir, 'docker-compose.yml')
        with open(compose_path, 'w', encoding='utf-8') as f:
            f.write(compose_content)

        def append_log(line, level='info'):
            with task_lock:
                deploy_tasks[task_id]['logs'].append({'t': time.time(), 'msg': line, 'level': level})

        def set_progress(pct, phase='pull'):
            with task_lock:
                deploy_tasks[task_id]['pull_progress'] = pct
                deploy_tasks[task_id]['phase'] = phase

        append_log('📁 创建临时目录: ' + tmpdir)
        cmd = ['docker', 'compose', '-f', compose_path]
        if project_name:
            cmd += ['-p', project_name]

        if pull_first:
            append_log('📥 开始拉取最新镜像...')
            set_progress(0, 'pull')
            pull_ok, pull_errors = _run_pull_with_progress(task_id, cmd, append_log, lambda p: set_progress(p, 'pull'))
            if not pull_ok:
                append_log('⚠️ 镜像拉取出现错误（将继续尝试启动）', 'warn')
                # 如果有网络错误，直接标记失败，不浪费时间再 up
                if pull_errors and any('deadline exceeded' in e or 'i/o timeout' in e or 'registry-1.docker.io' in e
                                       for e in (e.lower() for e in pull_errors)):
                    append_log('❌ 网络连接 Docker Hub 失败，终止部署', 'error')
                    for err in pull_errors:
                        append_log('  • ' + err, 'error')
                    with task_lock:
                        deploy_tasks[task_id]['status'] = 'failed'
                        deploy_tasks[task_id]['phase'] = 'done'
                        deploy_tasks[task_id]['error_summary'] = pull_errors
                    return

        set_progress(100, 'deploy')
        append_log('🚀 启动容器...')
        up_result = subprocess.run(cmd + ['up', '-d', '--remove-orphans'], capture_output=True, text=True, timeout=300)

        all_output_lines = (up_result.stdout + up_result.stderr).splitlines()
        for line in all_output_lines:
            if line.strip():
                level = 'error' if 'error' in line.lower() else 'warn' if 'warn' in line.lower() else 'info'
                append_log(line, level)

        if up_result.returncode == 0:
            append_log('✅ 部署成功！', 'success')
            with task_lock:
                deploy_tasks[task_id]['status'] = 'success'
                deploy_tasks[task_id]['phase'] = 'done'
        else:
            # 提取错误摘要
            error_summary = _extract_error_summary(all_output_lines)
            if not error_summary:
                error_summary = [f'进程退出码: {up_result.returncode}，请查看上方日志获取详情']

            append_log('❌ 部署失败，退出码: ' + str(up_result.returncode), 'error')
            append_log('── 失败原因摘要 ──', 'error')
            for err in error_summary:
                append_log('  • ' + err, 'error')

            with task_lock:
                deploy_tasks[task_id]['status'] = 'failed'
                deploy_tasks[task_id]['phase'] = 'done'
                deploy_tasks[task_id]['error_summary'] = error_summary

    except subprocess.TimeoutExpired:
        summary = ['部署超时（超过5分钟）— 可能原因：镜像过大、网络缓慢、容器启动卡死']
        with task_lock:
            deploy_tasks[task_id]['status'] = 'failed'
            deploy_tasks[task_id]['error_summary'] = summary
            deploy_tasks[task_id]['logs'].append({'t': time.time(), 'msg': '❌ 超时（5分钟），任务终止', 'level': 'error'})
            deploy_tasks[task_id]['logs'].append({'t': time.time(), 'msg': '  • ' + summary[0], 'level': 'error'})
    except Exception as e:
        summary = [str(e)]
        with task_lock:
            deploy_tasks[task_id]['status'] = 'failed'
            deploy_tasks[task_id]['error_summary'] = summary
            deploy_tasks[task_id]['logs'].append({'t': time.time(), 'msg': f'❌ 异常: {e}', 'level': 'error'})
    finally:
        threading.Timer(60, lambda: shutil.rmtree(tmpdir, ignore_errors=True)).start()


def run_compose_down(task_id, project_name, remove_volumes):
    try:
        def append_log(line, level='info'):
            with task_lock:
                deploy_tasks[task_id]['logs'].append({'t': time.time(), 'msg': line, 'level': level})

        cmd = ['docker', 'compose', '-p', project_name, 'down']
        if remove_volumes:
            cmd.append('-v')

        append_log(f'🛑 正在停止项目 {project_name}...')
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)

        for line in (result.stdout + result.stderr).splitlines():
            if line.strip():
                append_log(line)

        if result.returncode == 0:
            append_log('✅ 项目已停止并移除', 'success')
            with task_lock:
                deploy_tasks[task_id]['status'] = 'success'
        else:
            append_log('❌ 操作失败，退出码: ' + str(result.returncode), 'error')
            with task_lock:
                deploy_tasks[task_id]['status'] = 'failed'
    except Exception as e:
        with task_lock:
            deploy_tasks[task_id]['status'] = 'failed'
            deploy_tasks[task_id]['logs'].append({'t': time.time(), 'msg': f'❌ 异常: {e}', 'level': 'error'})


def main():
    load_or_create_secret()
    port = int(os.environ.get('BACKEND_PORT', '8081'))
    server = HTTPServer(('0.0.0.0', port), Handler)
    print(f'Docker Manager Backend v2.2.0 listening on :{port}')
    server.serve_forever()


if __name__ == '__main__':
    main()
