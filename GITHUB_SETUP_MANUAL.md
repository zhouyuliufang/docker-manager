# GitHub SSH 发布配置 - 手动操作指南

## 第 1 步：生成 SSH 密钥

**打开 Git Bash**（安装 Git for Windows 后右键菜单有）

```bash
# 生成新密钥
ssh-keygen -t ed25519 -C "13983418126@163.com"

# 按三次回车（使用默认路径，不设置密码）
# 密钥将保存在: C:\Users\zylf\.ssh\id_ed25519
```

**查看公钥内容**：
```bash
cat ~/.ssh/id_ed25519.pub
```

复制输出的全部内容（以 ssh-ed25519 开头，以邮箱结尾）

---

## 第 2 步：添加公钥到 GitHub

1. 访问：https://github.com/settings/keys
2. 点击 **"New SSH key"**
3. 填写：
   - **Title**: `Docker Manager Key`
   - **Key type**: `Authentication Key`
   - **Key**: 粘贴刚才复制的公钥内容
4. 点击 **"Add SSH key"**

---

## 第 3 步：测试 SSH 连接

在 Git Bash 中运行：

```bash
ssh -T git@github.com
```

应该看到：
```
Hi zhouyuliufang! You've successfully authenticated, but GitHub does not provide shell access.
```

---

## 第 4 步：初始化 Git 仓库并推送

**打开命令提示符或 PowerShell**：

```bash
cd "C:\Users\zylf\WorkBuddy\Claw\docker-manager"

# 初始化仓库（如果还没初始化）
git init

# 配置用户信息
git config user.email "13983418126@163.com"
git config user.name "zhouyuliufang"

# 添加远程仓库
git remote add origin git@github.com:zhouyuliufang/docker-manager.git

# 拉取最新代码（避免冲突）
git pull origin main --rebase

# 添加文件
git add backend.py index.html docker-compose.yml Dockerfile entrypoint.sh nginx.conf PUBLISH.md publish_*.py

# 提交
git commit -m "Release v3.1.0 - Add image management and 11 new apps"

# 推送到 GitHub
git push -u origin main
```

---

## 第 5 步：验证发布

访问：https://github.com/zhouyuliufang/docker-manager

应该能看到刚刚推送的文件。

---

## 故障排除

### 问题 1: `git: command not found`

**解决**：安装 Git for Windows
- 下载：https://git-scm.com/download/win
- 安装时勾选 "Git Bash Here"

### 问题 2: `Permission denied (publickey)`

**解决**：
1. 确认公钥已正确添加到 GitHub
2. 启动 SSH agent：
   ```bash
   # Git Bash 中运行
   eval $(ssh-agent -s)
   ssh-add ~/.ssh/id_ed25519
   ```
3. 重新测试：`ssh -T git@github.com`

### 问题 3: `src refspec main does not match any`

**解决**：
```bash
# 先确保有提交
git commit -m "Initial commit" --allow-empty
git push -u origin main
```

### 问题 4: `fatal: refusing to merge unrelated histories`

**解决**：
```bash
git pull origin main --allow-unrelated-histories
```

---

## 替代方案：使用 HTTPS + Token

如果不想配置 SSH，可以使用 Token 方式：

1. 生成 Token: https://github.com/settings/tokens
2. 选择 `repo` 权限
3. 使用 publish_github.py 脚本：

```bash
cd "C:\Users\zylf\WorkBuddy\Claw\docker-manager"
python publish_github.py
```

输入 Token 即可自动发布。

---

完成以上步骤后，你的项目就成功发布到 GitHub 了！
