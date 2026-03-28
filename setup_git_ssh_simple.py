#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
自动配置 Git 和 SSH，然后发布到 GitHub
简化版：无 emoji，兼容 Windows GBK
"""
import os, sys, subprocess, webbrowser

EMAIL = "13983418126@163.com"
GITHUB_USERNAME = "zhouyuliufang"
REPO_NAME = "docker-manager"

SSH_DIR = os.path.join(os.path.expanduser("~"), ".ssh")
KEY_PATH = os.path.join(SSH_DIR, "id_ed25519")
PUB_KEY_PATH = KEY_PATH + ".pub"

def run_cmd(cmd, cwd=None):
    """执行命令"""
    try:
        result = subprocess.run(cmd, shell=True, cwd=cwd, capture_output=True, text=True, encoding='utf-8', errors='replace')
        return result.returncode == 0, result.stdout, result.stderr
    except Exception as e:
        return False, "", str(e)

def check_git():
    print("[1/6] 检查 Git 安装...")
    ok, out, _ = run_cmd("git --version")
    if ok:
        print("  [OK] Git 已安装: " + out.strip())
        return True
    else:
        print("  [FAIL] Git 未安装")
        print("\n请下载并安装 Git for Windows: https://git-scm.com/download/win")
        return False

def generate_ssh_key():
    print("\n[2/6] 生成 SSH 密钥...")
    os.makedirs(SSH_DIR, exist_ok=True)
    
    if os.path.exists(KEY_PATH):
        print("  [SKIP] SSH 密钥已存在: " + KEY_PATH)
        return True
    
    cmd = f'ssh-keygen -t ed25519 -C "{EMAIL}" -f "{KEY_PATH}" -N ""'
    ok, out, err = run_cmd(cmd)
    if ok:
        print("  [OK] SSH 密钥生成成功")
        print("       私钥: " + KEY_PATH)
        print("       公钥: " + PUB_KEY_PATH)
        return True
    else:
        print("  [FAIL] SSH 密钥生成失败: " + err)
        return False

def get_public_key():
    try:
        with open(PUB_KEY_PATH, 'r', encoding='utf-8') as f:
            return f.read().strip()
    except:
        return None

def add_key_to_github():
    print("\n[3/6] 添加公钥到 GitHub...")
    
    pubkey = get_public_key()
    if not pubkey:
        print("  [FAIL] 无法读取公钥")
        return False
    
    print("  公钥指纹: " + pubkey.split()[-1][:20] + "...")
    print("\n  正在打开 GitHub SSH 设置页面...")
    webbrowser.open("https://github.com/settings/keys")
    
    print("\n  请按以下步骤操作:")
    print("  1. 点击 'New SSH key'")
    print("  2. Title: Docker Manager Key")
    print("  3. Key type: Authentication Key")
    print("  4. Key: 粘贴以下内容（已复制到剪贴板）:")
    print("\n" + "="*60)
    print(pubkey)
    print("="*60 + "\n")
    
    # 复制到剪贴板
    try:
        subprocess.run("clip", input=pubkey.encode(), check=True)
        print("  [OK] 公钥已复制到剪贴板")
    except:
        print("  [WARN] 请手动复制上方公钥内容")
    
    input("  完成后按 Enter 继续...")
    return True

def test_ssh():
    print("\n[4/6] 测试 SSH 连接...")
    print("  执行: ssh -T git@github.com")
    
    ok, out, err = run_cmd("ssh -T git@github.com")
    output = out + err
    
    if "successfully authenticated" in output.lower():
        print("  [OK] SSH 连接成功")
        if GITHUB_USERNAME.lower() in output.lower():
            for line in output.split('\n'):
                if GITHUB_USERNAME.lower() in line.lower():
                    print("       " + line.strip())
                    break
        return True
    else:
        print("  [FAIL] SSH 连接失败")
        print("  输出: " + output[:200])
        print("\n  可能原因:")
        print("  1. 公钥未正确添加到 GitHub")
        print("  2. SSH agent 未启动")
        return False

def setup_repo():
    print("\n[5/6] 配置 Git 仓库...")
    
    ok, _, _ = run_cmd("git rev-parse --git-dir")
    if not ok:
        print("  初始化 Git 仓库...")
        run_cmd("git init")
    
    run_cmd('git config user.email "' + EMAIL + '"')
    run_cmd('git config user.name "' + GITHUB_USERNAME + '"')
    
    remote_url = f"git@github.com:{GITHUB_USERNAME}/{REPO_NAME}.git"
    ok, out, _ = run_cmd("git remote get-url origin")
    
    if not ok or remote_url not in out:
        print("  设置远程仓库: " + remote_url)
        run_cmd(f'git remote set-url origin {remote_url}')
    else:
        print("  远程仓库已配置: " + out.strip())
    
    print("  [OK] Git 仓库配置完成")
    return True

def push_to_github():
    print("\n[6/6] 推送到 GitHub...")
    
    print("  同步远程代码...")
    run_cmd("git pull origin main --rebase")
    
    files = ["backend.py", "index.html", "docker-compose.yml", "Dockerfile", "entrypoint.sh", "nginx.conf", "PUBLISH.md", "publish_github.py", "publish_ssh.py", "setup_git_ssh_simple.py"]
    print("  添加文件...")
    for f in files:
        if os.path.exists(f):
            run_cmd(f'git add {f}')
    
    message = "Release v3.1.0 - Add image management and 11 new apps"
    ok, _, _ = run_cmd(f'git commit -m "{message}"')
    if ok:
        print("  [OK] 提交成功")
    else:
        print("  [WARN] 无变更或提交失败")
    
    print("  推送中...")
    ok, out, err = run_cmd("git push origin main")
    
    if ok:
        print("\n[SUCCESS] 发布成功！")
        print(f"\n仓库地址: https://github.com/{GITHUB_USERNAME}/{REPO_NAME}")
        print(f"提交信息: {message}")
        return True
    else:
        print(f"\n[FAIL] 推送失败")
        print(f"错误: {err[:300]}")
        return False

def main():
    print("="*60)
    print("Docker Manager GitHub 发布配置")
    print("="*60)
    
    steps = [
        ("检查 Git", check_git),
        ("生成 SSH 密钥", generate_ssh_key),
        ("添加到 GitHub", add_key_to_github),
        ("测试 SSH 连接", test_ssh),
        ("配置仓库", setup_repo),
        ("推送代码", push_to_github),
    ]
    
    for i, (name, func) in enumerate(steps, 1):
        if not func():
            print(f"\n[STOP] 步骤 {i} 失败: {name}")
            return False
    
    print("\n" + "="*60)
    print("全部完成！项目已发布到 GitHub")
    print("="*60)
    return True

if __name__ == "__main__":
    try:
        success = main()
        sys.exit(0 if success else 1)
    except KeyboardInterrupt:
        print("\n\n操作已取消")
        sys.exit(1)
