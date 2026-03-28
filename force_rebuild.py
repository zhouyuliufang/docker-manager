#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""强制无缓存重建 docker-manager 镜像"""
import io, sys, time, paramiko

if hasattr(sys.stdout, 'buffer'):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

NAS_HOST   = "192.168.0.126"
NAS_PORT   = 44444
NAS_USER   = "13983418126"
NAS_PASS   = "Zhouyu730126"
REMOTE_DIR = "/tmp/zfsv3/nvme19/13983418126/data/Docker/docker-manager"

client = paramiko.SSHClient()
client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
client.connect(NAS_HOST, port=NAS_PORT, username=NAS_USER, password=NAS_PASS, timeout=10)
print(f"[OK] 已连接 {NAS_HOST}")

def sudo(cmd, timeout=300, show=True):
    full = f"echo {NAS_PASS} | sudo -S bash -c \"{cmd}\""
    if show:
        print(f"  $ (sudo) {cmd}")
    _, o, e = client.exec_command(full, timeout=timeout)
    out = o.read().decode(errors='replace').strip()
    err = e.read().decode(errors='replace').strip()
    for line in (out + "\n" + err).splitlines():
        if "password" in line.lower():
            continue
        if line.strip():
            print(f"    {line}")
    return out

def run(cmd, timeout=30):
    _, o, e = client.exec_command(cmd, timeout=timeout)
    return o.read().decode(errors='replace').strip()

# ── 1. 先停掉旧容器
print("\n[1] 停止旧容器...")
sudo(f"cd {REMOTE_DIR} && docker-compose down 2>&1", timeout=60)

# ── 2. 无缓存重建
print("\n[2] 无缓存重建镜像 (--no-cache，约 2~5 分钟)...")
print("    请稍候...")
sudo(f"cd {REMOTE_DIR} && docker-compose build --no-cache 2>&1", timeout=360)

# ── 3. 启动
print("\n[3] 启动容器...")
sudo(f"cd {REMOTE_DIR} && docker-compose up -d 2>&1", timeout=60)

# ── 4. 等待
print("\n[4] 等待启动 (10s)...")
time.sleep(10)

# ── 5. 验证状态
print("\n[5] 验证容器状态...")
sudo("docker ps --filter name=docker-manager --format '{{.Names}}\\t{{.Status}}'")

# ── 6. 验证 HTTP
print("\n[6] 验证 HTTP...")
code = run("curl -s -o /dev/null -w '%{http_code}' http://localhost:9999/")
print(f"    HTTP 状态码: {code}")

# ── 7. 验证版本号
print("\n[7] 验证容器内版本信息...")
ver = sudo("docker exec docker-manager grep -o 'v[0-9]*\\.[0-9]*\\.[0-9]*' /usr/share/nginx/html/index.html 2>/dev/null | head -3", show=False)
print(f"    容器内版本号: {ver if ver else '(未找到)'}")

# ── 8. 验证 imageUpdateCache
print("\n[8] 验证新功能关键字...")
cnt = sudo("docker exec docker-manager grep -c 'imageUpdateCache' /usr/share/nginx/html/index.html 2>/dev/null", show=False)
cnt = cnt.strip() if cnt else "0"
if cnt.isdigit() and int(cnt) > 0:
    print(f"    [OK] imageUpdateCache 出现 {cnt} 次，新功能已生效")
else:
    print(f"    [WARN] imageUpdateCache={cnt}，容器内文件可能仍未更新")

# ── 9. 对比本地 vs 容器内 index.html 大小
print("\n[9] 对比文件大小...")
local_size = sudo(f"wc -c < {REMOTE_DIR}/index.html", show=False).strip()
inner_size = sudo("docker exec docker-manager wc -c < /usr/share/nginx/html/index.html 2>/dev/null", show=False).strip()
print(f"    NAS 上:  {local_size} bytes")
print(f"    容器内:  {inner_size} bytes")
if local_size == inner_size:
    print("    [OK] 文件一致！")
else:
    print("    [WARN] 文件大小不一致，可能仍有缓存问题")

client.close()
print("\n[DONE] 完成！访问 http://192.168.0.126:9999")
