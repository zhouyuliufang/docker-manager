#!/bin/sh
# 启动脚本：同时启动 Python 后端和 Nginx

echo "🐳 Docker 管理中心启动..."

# 启动 Python 后端（后台）
python3 /app/backend.py &
BACKEND_PID=$!
echo "✅ Python 后端已启动 (PID: $BACKEND_PID)"

# 等待后端就绪
sleep 1

# 启动 Nginx（前台，保持容器运行）
echo "✅ Nginx 启动中..."
nginx -g "daemon off;"
