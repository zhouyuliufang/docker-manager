FROM python:3.11-alpine

# 安装 nginx、docker-compose、curl
RUN apk add --no-cache nginx curl bash \
    && pip install --no-cache-dir requests

# 安装 docker CLI 和 compose 插件
RUN apk add --no-cache docker-cli docker-cli-compose

# 创建数据目录
RUN mkdir -p /data /var/log/nginx /run/nginx

# 复制配置文件
COPY nginx.conf /etc/nginx/nginx.conf
COPY index.html /usr/share/nginx/html/index.html
COPY backend.py /app/backend.py
COPY entrypoint.sh /entrypoint.sh

RUN chmod +x /entrypoint.sh

WORKDIR /app

EXPOSE 80

CMD ["/entrypoint.sh"]
