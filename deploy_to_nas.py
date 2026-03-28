#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
部署 Docker 管理中心到极空间 NAS
工具优先级：plink (PuTTY) -> paramiko (Python SSH)
流程：上传文件 -> docker-compose up --build -d -> 验证
"""
import os, sys, time, io, shutil, subprocess, tempfile

if hasattr(sys.stdout, 'buffer'):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
if hasattr(sys.stderr, 'buffer'):
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')

# ── 连接配置 ────────────────────────────────────────────────
NAS_HOSTS      = ["192.168.0.126", "192.168.0.24", "10.20.30.1", "100.66.1.3"]
NAS_PORT       = 44444
NAS_USER       = "13983418126"
NAS_PASS       = "Zhouyu730126"
REMOTE_DIR     = "/tmp/zfsv3/nvme19/13983418126/data/Docker/docker-manager"
LOCAL_DIR      = os.path.dirname(os.path.abspath(__file__))
CONTAINER_NAME = "docker-manager"
UPLOAD_FILES   = [
    "index.html",
    "backend.py",
    "nginx.conf",
    "docker-compose.yml",
    "Dockerfile",
    "entrypoint.sh",
]

# ════════════════════════════════════════════════════════════
#  工具检测
# ════════════════════════════════════════════════════════════
PLINK_PATH  = shutil.which("plink")
PSCP_PATH   = shutil.which("pscp")   # PuTTY SCP，用于文件上传
USE_PLINK   = bool(PLINK_PATH)       # 本地有 plink 就用

def _detect_tool():
    if USE_PLINK:
        print(f"  [tool] plink  -> {PLINK_PATH}")
        if PSCP_PATH:
            print(f"  [tool] pscp   -> {PSCP_PATH}")
        else:
            print("  [tool] pscp   -> 未找到，文件上传将 fallback 到 paramiko SFTP")
    else:
        print("  [tool] plink  -> 未找到，使用 paramiko (Python SSH)")

# ════════════════════════════════════════════════════════════
#  plink 实现
# ════════════════════════════════════════════════════════════
_plink_host = None   # 连接成功的 host

def plink_connect(hosts):
    """探测可连接的 host，返回第一个成功的 host"""
    global _plink_host
    for host in hosts:
        print(f"[1] 尝试连接 {host}:{NAS_PORT} ...")
        try:
            result = subprocess.run(
                [PLINK_PATH, "-ssh",
                 "-P", str(NAS_PORT),
                 "-l", NAS_USER,
                 "-pw", NAS_PASS,
                 "-batch",           # 非交互，不询问 host key
                 "-hostkey", "**",   # 接受任意 host key
                 f"{host}",
                 "echo OK"],
                capture_output=True, text=True, timeout=12, encoding='utf-8', errors='replace'
            )
            if "OK" in result.stdout:
                _plink_host = host
                print(f"  [OK] 连接成功: {host}\n")
                return host
            else:
                print(f"  [FAIL] stdout={result.stdout.strip()!r} stderr={result.stderr.strip()!r}")
        except Exception as e:
            print(f"  [FAIL] {e}")
    return None

def plink_run(cmd, show=True, timeout=300):
    """用 plink 执行远端命令，返回 (stdout, stderr)"""
    if show:
        print(f"  $ {cmd}")
    result = subprocess.run(
        [PLINK_PATH, "-ssh",
         "-P", str(NAS_PORT),
         "-l", NAS_USER,
         "-pw", NAS_PASS,
         "-batch",
         "-hostkey", "**",
         _plink_host,
         cmd],
        capture_output=True, text=True, timeout=timeout, encoding='utf-8', errors='replace'
    )
    out = result.stdout.strip()
    err = result.stderr.strip()
    err_clean = '\n'.join(l for l in err.splitlines()
                          if 'password' not in l.lower() and l.strip())
    if show and out:
        print(f"    {out}")
    if show and err_clean:
        print(f"    [err] {err_clean}")
    return out, err

def plink_sudo(cmd, show=True, timeout=300):
    """用 plink 以 sudo 执行命令"""
    full = f"echo {NAS_PASS} | sudo -S bash -c \"{cmd}\""
    if show:
        print(f"  $ (sudo) {cmd}")
    result = subprocess.run(
        [PLINK_PATH, "-ssh",
         "-P", str(NAS_PORT),
         "-l", NAS_USER,
         "-pw", NAS_PASS,
         "-batch",
         "-hostkey", "**",
         _plink_host,
         full],
        capture_output=True, text=True, timeout=timeout, encoding='utf-8', errors='replace'
    )
    out = result.stdout.strip()
    err = result.stderr.strip()
    err_clean = '\n'.join(l for l in err.splitlines()
                          if 'password' not in l.lower() and l.strip())
    if show and out:
        print(f"    {out}")
    if show and err_clean:
        print(f"    [err] {err_clean}")
    return out, err

def plink_upload(files):
    """用 pscp（或 paramiko fallback）上传文件"""
    if PSCP_PATH:
        for fname in files:
            local_path = os.path.join(LOCAL_DIR, fname)
            if not os.path.exists(local_path):
                print(f"  [SKIP] 本地不存在: {fname}")
                continue
            remote_path = f"{REMOTE_DIR}/{fname}"
            result = subprocess.run(
                [PSCP_PATH,
                 "-P", str(NAS_PORT),
                 "-pw", NAS_PASS,
                 "-batch",
                 "-hostkey", "**",
                 local_path,
                 f"{NAS_USER}@{_plink_host}:{remote_path}"],
                capture_output=True, text=True, timeout=60, encoding='utf-8', errors='replace'
            )
            size = os.path.getsize(local_path)
            if result.returncode == 0:
                print(f"  [OK] {fname}  ({size:,} bytes)")
            else:
                print(f"  [FAIL] {fname}: {result.stderr.strip()}")
    else:
        # pscp 不存在，fallback 到 paramiko SFTP 上传
        print("  [info] pscp 未找到，fallback 到 paramiko SFTP 上传 ...")
        _paramiko_upload_only()

# ════════════════════════════════════════════════════════════
#  paramiko 实现
# ════════════════════════════════════════════════════════════
_paramiko_client = None

def paramiko_connect(hosts):
    global _paramiko_client
    import paramiko
    for host in hosts:
        print(f"[1] 尝试连接 {host}:{NAS_PORT} ...")
        try:
            client = paramiko.SSHClient()
            client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            client.connect(host, port=NAS_PORT, username=NAS_USER, password=NAS_PASS, timeout=10)
            _paramiko_client = client
            print(f"  [OK] 连接成功: {host}\n")
            return host
        except Exception as e:
            print(f"  [FAIL] {e}")
    return None

def paramiko_run(cmd, show=True, timeout=300):
    if show:
        print(f"  $ {cmd}")
    _, stdout, stderr = _paramiko_client.exec_command(cmd, timeout=timeout)
    out = stdout.read().decode(errors='replace').strip()
    err = stderr.read().decode(errors='replace').strip()
    err_clean = '\n'.join(l for l in err.splitlines()
                          if 'password' not in l.lower() and l.strip())
    if show and out:
        print(f"    {out}")
    if show and err_clean:
        print(f"    [err] {err_clean}")
    return out, err

def paramiko_sudo(cmd, show=True, timeout=300):
    full = f"echo {NAS_PASS} | sudo -S bash -c \"{cmd}\""
    if show:
        print(f"  $ (sudo) {cmd}")
    _, stdout, stderr = _paramiko_client.exec_command(full, timeout=timeout)
    out = stdout.read().decode(errors='replace').strip()
    err = stderr.read().decode(errors='replace').strip()
    err_clean = '\n'.join(l for l in err.splitlines()
                          if 'password' not in l.lower() and l.strip())
    if show and out:
        print(f"    {out}")
    if show and err_clean:
        print(f"    [err] {err_clean}")
    return out, err

def paramiko_upload(files):
    sftp = _paramiko_client.open_sftp()
    try:
        for fname in files:
            local_path  = os.path.join(LOCAL_DIR, fname)
            remote_path = f"{REMOTE_DIR}/{fname}"
            if not os.path.exists(local_path):
                print(f"  [SKIP] 本地不存在: {fname}")
                continue
            sftp.put(local_path, remote_path)
            size = os.path.getsize(local_path)
            print(f"  [OK] {fname}  ({size:,} bytes)")
    finally:
        sftp.close()

def _paramiko_upload_only():
    """仅上传（在 plink 模式下 pscp 缺失时的 fallback）"""
    import paramiko
    global _paramiko_client
    tmp = paramiko.SSHClient()
    tmp.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    tmp.connect(_plink_host, port=NAS_PORT, username=NAS_USER, password=NAS_PASS, timeout=10)
    sftp = tmp.open_sftp()
    try:
        for fname in UPLOAD_FILES:
            local_path  = os.path.join(LOCAL_DIR, fname)
            remote_path = f"{REMOTE_DIR}/{fname}"
            if not os.path.exists(local_path):
                print(f"  [SKIP] 本地不存在: {fname}")
                continue
            sftp.put(local_path, remote_path)
            size = os.path.getsize(local_path)
            print(f"  [OK] {fname}  ({size:,} bytes)")
    finally:
        sftp.close()
        tmp.close()

def paramiko_close():
    if _paramiko_client:
        _paramiko_client.close()

# ════════════════════════════════════════════════════════════
#  统一接口（根据工具自动路由）
# ════════════════════════════════════════════════════════════
def do_connect(hosts):
    if USE_PLINK:
        return plink_connect(hosts)
    return paramiko_connect(hosts)

def do_run(cmd, show=True, timeout=300):
    if USE_PLINK:
        return plink_run(cmd, show=show, timeout=timeout)
    return paramiko_run(cmd, show=show, timeout=timeout)

def do_sudo(cmd, show=True, timeout=300):
    if USE_PLINK:
        return plink_sudo(cmd, show=show, timeout=timeout)
    return paramiko_sudo(cmd, show=show, timeout=timeout)

def do_upload():
    if USE_PLINK:
        plink_upload(UPLOAD_FILES)
    else:
        paramiko_upload(UPLOAD_FILES)

def do_close():
    if not USE_PLINK:
        paramiko_close()

# ════════════════════════════════════════════════════════════
#  主流程
# ════════════════════════════════════════════════════════════
def main():
    print("\n======================================")
    print("  Docker 管理中心 -> 极空间 NAS 部署  ")
    print("======================================")
    _detect_tool()
    print()

    # 1. 连接
    host = do_connect(NAS_HOSTS)
    if not host:
        print("\n[ERROR] 所有 IP 均无法连接，请检查网络")
        return False

    try:
        # 2. 上传文件
        print("[2] 上传源码文件到 NAS ...")
        do_upload()

        # 3. 文件权限
        print("\n[3] 设置文件权限 ...")
        do_run(
            f"chmod 644 {REMOTE_DIR}/index.html "
            f"{REMOTE_DIR}/backend.py "
            f"{REMOTE_DIR}/nginx.conf "
            f"{REMOTE_DIR}/Dockerfile"
        )
        do_run(f"chmod 755 {REMOTE_DIR}/entrypoint.sh")

        # 4. 重新构建镜像（关键！代码是 COPY 进镜像的）
        print("\n[4] docker-compose up --build -d (重建镜像，约 1~3 分钟) ...")
        print("    请稍候...\n")
        out, err = do_sudo(
            f"cd {REMOTE_DIR} && docker-compose up --build -d 2>&1",
            show=False,
            timeout=300
        )
        for line in (out + '\n' + err).splitlines():
            if 'password' in line.lower():
                continue
            if line.strip():
                print(f"    {line}")

        # 5. 等待启动
        print("\n[5] 等待容器启动（8s）...")
        time.sleep(8)

        # 6. 验证容器状态
        print("\n[6] 验证容器状态 ...")
        out, _ = do_sudo(
            f"docker ps --filter name={CONTAINER_NAME} "
            f"--format '{{{{.Names}}}}\\t{{{{.Status}}}}'"
        )
        if "Up" in out:
            print(f"  [OK] 容器运行中: {out}")
        else:
            print("  [WARN] 容器状态异常，查看日志:")
            do_sudo(f"docker logs --tail 30 {CONTAINER_NAME}")
            return False

        # 7. 验证 HTTP
        print("\n[7] 验证 HTTP 接口 ...")
        out, _ = do_run("curl -s -o /dev/null -w '%{http_code}' http://localhost:9999/")
        code = out.strip()
        if code in ("200", "401", "403"):
            print(f"  [OK] HTTP 响应正常（状态码: {code}）")
        else:
            print(f"  [WARN] HTTP 状态码: {code}")

        # 8. 验证容器内文件已更新（检查最新功能关键字）
        print("\n[8] 验证容器内文件已更新 ...")
        out, _ = do_sudo(
            "docker exec docker-manager "
            "grep -c 'imageUpdateCache' /usr/share/nginx/html/index.html 2>/dev/null"
        )
        count = out.strip()
        if count and count.isdigit() and int(count) > 0:
            print(f"  [OK] 新功能已生效（imageUpdateCache 出现 {count} 次）")
        else:
            print(f"  [WARN] 容器内文件可能未更新（count={count}）")

        print("\n[DONE] 部署完成！")
        print(f"  访问地址: http://192.168.0.126:9999")
        print(f"  工具:     {'plink' if USE_PLINK else 'paramiko'}")
        return True

    finally:
        do_close()

if __name__ == "__main__":
    ok = main()
    sys.exit(0 if ok else 1)
