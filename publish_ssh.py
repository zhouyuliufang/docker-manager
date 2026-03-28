#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
使用 SSH 方式发布到 GitHub
自动执行 git add/commit/push
"""
import os, sys, subprocess, datetime

# 配置
PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_URL = "git@github.com:zhouyuliufang/docker-manager.git"
FILES_TO_COMMIT = ["backend.py", "index.html", "docker-compose.yml", "Dockerfile", "entrypoint.sh", "nginx.conf", "PUBLISH.md"]

def run_cmd(cmd, cwd=None, capture=True):
    """执行 shell 命令"""
    print(f"  > {cmd}")
    try:
        result = subprocess.run(cmd, shell=True, cwd=cwd, capture_output=capture, text=True, encoding='utf-8')
        if result.returncode != 0 and capture:
            print(f"    [ERROR] {result.stderr}")
        return result.returncode == 0, result.stdout, result.stderr
    except Exception as e:
        print(f"    [EXCEPTION] {e}")
        return False, "", str(e)

def main():
    print(f"\n[GitHub SSH Publisher] 发布到 {REPO_URL}\n")
    
    os.chdir(PROJECT_DIR)
    
    # 1. 检查 git 仓库
    print("[1/6] 检查 Git 仓库...")
    ok, _, _ = run_cmd("git status")
    if not ok:
        print("\n❌ 当前目录不是 Git 仓库或 git 未安装")
        print("\n解决方案：")
        print("  1. git init")
        print(f"  2. git remote add origin {REPO_URL}")
        print("  3. 重新运行本脚本")
        return False
    
    # 2. 检查远程地址
    print("\n[2/6] 检查远程仓库地址...")
    ok, stdout, _ = run_cmd("git remote get-url origin")
    if not ok or REPO_URL not in stdout:
        print(f"  设置远程地址: {REPO_URL}")
        run_cmd(f"git remote set-url origin {REPO_URL}")
    else:
        print(f"  远程地址已正确: {stdout.strip()}")
    
    # 3. 检查 SSH 认证
    print("\n[3/6] 测试 SSH 连接 GitHub...")
    print("  执行: ssh -T git@github.com")
    ok, stdout, stderr = run_cmd("ssh -T git@github.com")
    if not ok or "successfully authenticated" not in (stdout + stderr).lower():
        print("\n⚠️  SSH 认证失败或未配置")
        print("\n请确保：")
        print("  1. SSH 密钥已生成: ssh-keygen -t ed25519 -C 'your_email@example.com'")
        print("  2. 公钥已添加到 GitHub: https://github.com/settings/keys")
        print("  3. SSH agent 已启动: eval $(ssh-agent -s) && ssh-add ~/.ssh/id_ed25519")
        print("\n或者使用 HTTPS + Token 方式: python publish_github.py")
        return False
    print("  ✅ SSH 认证成功")
    
    # 4. 拉取最新代码（避免冲突）
    print("\n[4/6] 同步远程最新代码...")
    ok, _, stderr = run_cmd("git pull origin main --rebase")
    if not ok and "CONFLICT" in stderr:
        print("\n❌ 检测到冲突，请手动解决后再发布")
        return False
    print("  ✅ 同步完成")
    
    # 5. 添加文件
    print("\n[5/6] 添加文件到 Git...")
    for f in FILES_TO_COMMIT:
        if os.path.exists(f):
            ok, _, _ = run_cmd(f"git add {f}")
            print(f"  {'✅' if ok else '❌'} {f}")
    
    # 6. 提交并推送
    print("\n[6/6] 提交并推送到 GitHub...")
    version = "v3.1.0"
    message = f"Release {version} - Add image management and 11 new apps"
    
    ok, _, _ = run_cmd(f"git commit -m '{message}'")
    if ok:
        print("  ✅ 提交成功")
    else:
        print("  ⚠️  没有变更或提交失败，尝试强制推送当前状态")
    
    print("  正在推送...")
    ok, stdout, stderr = run_cmd("git push origin main")
    if ok:
        print("\n🎉 发布成功！")
        print(f"\n查看仓库: https://github.com/zhouyuliufang/docker-manager")
        print(f"最近提交: {message}")
        return True
    else:
        print(f"\n❌ 推送失败: {stderr}")
        return False

if __name__ == "__main__":
    try:
        success = main()
        sys.exit(0 if success else 1)
    except KeyboardInterrupt:
        print("\n\n取消发布")
        sys.exit(1)
