# 发布到 GitHub 说明

## 步骤 1: 准备 GitHub Personal Access Token

1. 访问 https://github.com/settings/tokens
2. 点击 "Generate new token (classic)"
3. 选择 scopes: `repo` (全选)
4. 生成 token 并复制（只显示一次）

## 步骤 2: 运行发布脚本

```bash
cd "C:\Users\zylf\WorkBuddy\Claw\docker-manager"
python publish_github.py
```

脚本会提示输入 token，粘贴后回车即可。

## 步骤 3: 验证发布

访问 https://github.com/zhouyuliufang/docker-manager 查看文件是否更新。

## 替代方案（使用 Git 命令）

如果上述方法失败，可以手动使用 git：

```bash
git clone https://github.com/zhouyuliufang/docker-manager.git
cd docker-manager
cp /path/to/local/files/* ./
git add .
git commit -m "Update v3.0.0 - Add image management and more apps"
git push origin main
```
