#!/bin/sh
# 启动脚本：同时启动 Python 后端和 Nginx

echo "Docker 管理中心启动..."

# 动态获取 docker.sock 的 GID，并将 nginx 用户加入该组
# 这样 nginx 才能通过 unix socket 访问 Docker API
if [ -S /var/run/docker.sock ]; then
    DOCKER_GID=$(stat -c '%g' /var/run/docker.sock)
    echo "docker.sock GID: $DOCKER_GID"
    # 如果该 GID 的组不存在则创建
    if ! getent group $DOCKER_GID > /dev/null 2>&1; then
        addgroup -g $DOCKER_GID dockersock
    fi
    DOCKER_GROUP=$(getent group $DOCKER_GID | cut -d: -f1)
    # 将 nginx 用户加入该组
    adduser nginx $DOCKER_GROUP 2>/dev/null || true
    echo "nginx 用户已加入组: $DOCKER_GROUP ($DOCKER_GID)"
fi

# 启动 Python 后端（后台）
python3 /app/backend.py &
BACKEND_PID=$!
echo "Python 后端已启动 (PID: $BACKEND_PID)"

# 等待后端就绪
sleep 1

# 启动 Nginx（前台，保持容器运行）
echo "Nginx 启动中..."
nginx -g "daemon off;"
