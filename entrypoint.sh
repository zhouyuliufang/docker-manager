#!/bin/bash
set -e

echo "[entrypoint] Docker 管理中心 v3.0.0 启动中..."
echo "[entrypoint] 数据目录: /data"

# 确保数据目录存在（容器重建时自动创建）
mkdir -p /data

# 启动 nginx
echo "[entrypoint] 启动 nginx..."
nginx

# 启动后端
echo "[entrypoint] 启动后端服务 (port 8081)..."
exec python3 /app/backend.py
