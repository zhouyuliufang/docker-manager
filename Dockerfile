FROM python:3.11-alpine

# 安装 Nginx 和 Docker CLI
RUN apk add --no-cache nginx docker-cli docker-cli-compose curl

# 复制前端文件
COPY index.html /usr/share/nginx/html/index.html

# 复制配置文件
COPY nginx.conf /etc/nginx/nginx.conf

# 复制后端服务
COPY backend.py /app/backend.py

# 创建 nginx 运行目录
RUN mkdir -p /run/nginx

# 启动脚本：同时运行 Python 后端 + Nginx
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

EXPOSE 8080

CMD ["/entrypoint.sh"]
