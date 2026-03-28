# Docker 管理中心

> 一个轻量级、单文件架构的 Docker 可视化管理工具，专为家用 NAS / 私有服务器设计。

[![Version](https://img.shields.io/badge/version-3.1.0-blue.svg)](https://github.com/zhouyuliufang/docker-manager)
[![License](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.8+-yellow.svg)](https://python.org)

---

## 功能特性

### 容器管理
- 查看所有容器（运行中 / 已停止）的状态、端口、镜像信息
- 一键启动 / 停止 / 重启 / 删除容器
- 实时日志流（EventSource SSE，无需刷新页面）
- 容器搜索过滤（按名称、镜像、状态）

### 镜像管理（v3.1.0 新增）
- 本地镜像列表展示（大小、创建时间、ID）
- 一键检查 Docker Hub 最新更新状态
- 显示镜像最后更新时间及距今天数

### 资源监控
- 实时 CPU / 内存 / 网络 IO 使用率（5 秒自动刷新）
- 磁盘使用进度条
- 基于 `docker stats` 流式读取

### Compose 部署
- 粘贴 Docker Compose 内容一键部署
- 实时拉取进度条（基于 BuildKit 层解析）
- 部署失败原因自动提取展示
- 已保存的 Compose 文件管理（持久化至 `/data`）
- 部署历史记录（最多保留 50 条）

### 应用商店（26 款）
一键填充 Compose 模板并跳转部署页，覆盖以下分类：

| 分类 | 应用 |
|------|------|
| 数据库 | MySQL、PostgreSQL、MariaDB、MongoDB、Redis |
| 存储 | MinIO（S3 兼容对象存储） |
| 媒体 | Jellyfin、Immich、Calibre Web |
| 文件 | FileBrowser、Syncthing |
| 网络 | Nginx、AdGuard Home |
| 监控 | Grafana、Uptime Kuma、Change Detection |
| AI | Ollama、n8n |
| 开发工具 | Gitea |
| 低代码 | NocoDB |
| 工具 | Vaultwarden、Linkding |
| 运维 | Portainer |
| 协作 | Nextcloud |
| 智能家居 | Home Assistant |

### 安全与认证
- 单用户密码认证，密码持久化至 `/data/auth.json`
- JWT 令牌（7 天有效期），重建容器不丢失登录状态
- 支持在线修改密码
- 镜像加速器配置（通过后端 API 读写，不在前端暴露）

### 界面
- 深色 / 浅色主题切换（localStorage 持久化）
- 响应式布局，移动端友好

---

## 快速开始

### 使用 Docker Compose（推荐）

```yaml
version: '3'
services:
  docker-manager:
    image: zhouyuliufang/docker-manager:latest
    container_name: docker-manager
    ports:
      - "9999:80"
    volumes:
      - /var/run/docker.sock:/var/run/docker.sock
      - ./data:/data
    restart: unless-stopped
```

```bash
docker compose up -d
```

访问 `http://your-server-ip:9999`，首次访问设置登录密码。

### 手动构建运行

```bash
git clone https://github.com/zhouyuliufang/docker-manager.git
cd docker-manager
docker compose up -d --build
```

---

## 架构说明

```
浏览器
  │
  └── Nginx (80端口) → 对外映射 9999
        ├── /         → index.html（前端单文件）
        └── /api/*    → backend.py (8081端口，内部)
              └── Docker CLI（docker ps / docker stats 等）
```

| 组件 | 说明 |
|------|------|
| `index.html` | 纯 HTML + 原生 JS，无框架依赖 |
| `backend.py` | Python 3 标准库 HTTPServer，无需安装依赖 |
| `nginx.conf` | 静态文件 + API 反向代理 |
| `docker-compose.yml` | 一键部署配置 |

---

## API 接口

> 所有接口（除 `/api/auth/*`）均需 `Authorization: Bearer <token>` 请求头。

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/api/auth/login` | 登录 / 初始化密码 |
| POST | `/api/auth/change-password` | 修改密码 |
| GET | `/api/containers` | 获取容器列表 |
| POST | `/api/container/{id}/start` | 启动容器 |
| POST | `/api/container/{id}/stop` | 停止容器 |
| POST | `/api/container/{id}/restart` | 重启容器 |
| POST | `/api/container/{id}/remove` | 删除容器 |
| GET | `/api/container/{id}/logs` | 容器实时日志（SSE） |
| GET | `/api/images` | 获取本地镜像列表 |
| POST | `/api/images/check-update` | 检查镜像更新 |
| GET | `/api/stats` | 资源监控数据 |
| POST | `/api/deploy` | 部署 Compose |
| GET | `/api/task/{id}` | 任务状态查询 |
| GET | `/api/apps` | 应用商店列表 |
| GET | `/api/apps/{key}/template` | 获取应用 Compose 模板 |
| GET | `/api/history` | 部署历史 |
| GET/POST | `/api/composes` | 已保存的 Compose 文件 |
| GET/POST | `/api/mirrors` | 镜像加速器配置 |

---

## 数据持久化

所有持久化数据存储在 `/data` 目录（通过 `volumes` 挂载）：

| 文件 | 内容 |
|------|------|
| `auth.json` | 密码 Hash + JWT Secret |
| `deploy_history.json` | 部署历史（最多 50 条） |
| `saved_composes.json` | 已保存的 Compose 文件 |

---

## 版本历史

| 版本 | 日期 | 主要变更 |
|------|------|---------|
| v3.2.0 | 2026-03 | 镜像更新监控：容器管理页实时更新徽标、全量扫描、一键 pull + 重启容器 |
| v3.1.0 | 2026-03 | 镜像管理、更新检查、应用商店扩展至 26 款 |
| v3.0.0 | 2026-03 | 全量重构：容器管理增强、资源监控、主题切换、部署历史 |
| v2.2.4 | 2026-02 | 镜像加速器配置，兼容性修复 |
| v2.2.2 | 2026-01 | 部署失败原因提示 |
| v2.2.1 | 2026-01 | Compose 拉取进度条 |
| v2.2.0 | 2025-12 | 基础功能完善，单用户认证，应用商店 45 款 |

---

## 截图预览

> 深色主题 · 容器管理 · 资源监控 · 应用商店一键部署

---

## License

MIT License © 2026 [zhouyuliufang](https://github.com/zhouyuliufang)
