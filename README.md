# 🐳 Docker 管理中心

极空间 NAS Docker 管理界面，支持容器管理 + **Compose 一键部署**。

## 功能列表

### 🐋 容器管理
- 实时查看容器状态（运行中 / 已停止）
- 启动 / 停止 / 重启 / 删除容器
- 更新镜像（拉取最新版本）
- 实时查看容器日志（自动刷新）
- 搜索 / 过滤容器

### 🚀 Compose 部署（新功能）
- 在线编辑 docker-compose.yml
- 一键验证 compose 格式
- **一键部署**（自动拉取镜像 + docker compose up -d）
- 实时查看部署日志
- 停止并移除已部署项目
- 8个内置快速模板（Nginx / MySQL / Redis / PostgreSQL / Portainer / n8n / Ollama / Grafana）

---

## 部署到极空间

### 方式一：SSH 构建（推荐）

```bash
# 1. 上传 docker-manager 文件夹到极空间（替换为你的 IP、SSH端口、用户名）
scp -P <SSH_PORT> -r ./docker-manager <NAS_USER>@<NAS_IP>:/path/to/Docker/docker-manager

# 2. SSH 登录
ssh -p <SSH_PORT> <NAS_USER>@<NAS_IP>

# 3. 构建并启动
cd /path/to/Docker/docker-manager
docker compose up -d --build
```

访问：`http://<NAS_IP>:9999`

---

### 方式二：极空间 File Station 上传

1. 用极空间 File Station 把 `docker-manager` 文件夹上传到 Docker 数据目录

2. SSH 登录执行：
   ```bash
   cd /path/to/Docker/docker-manager
   docker compose up -d --build
   ```

---

## 文件结构

```
docker-manager/
├── index.html        # 前端界面
├── backend.py        # Python 后端（处理 Compose 部署）
├── nginx.conf        # Nginx 配置（前端服务 + API 代理）
├── Dockerfile        # 镜像构建文件
├── entrypoint.sh     # 启动脚本（Nginx + Python）
├── docker-compose.yml
└── README.md
```

---

## 架构说明

```
浏览器
  │
  ├─── GET /           → Nginx → index.html（前端）
  ├─── /docker-api/*   → Nginx → docker.sock（容器管理）
  └─── /api/*          → Nginx → Python:8081（Compose 部署）
                                    │
                                    └── 执行 docker compose 命令
```

---

## 注意事项

- docker.sock 需要挂载到容器（已在 docker-compose.yml 配置）
- Compose 部署使用异步任务，部署日志实时展示
- 数据卷建议挂载到 NAS 的 Docker 数据目录下
- 默认端口 `9999`，可在 docker-compose.yml 中修改
