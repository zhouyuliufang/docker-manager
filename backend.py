#!/usr/bin/env python3
"""
Docker 管理中心 - 后端服务
提供 Docker Compose 部署 API
"""
import os
import json
import subprocess
import tempfile
import shutil
import threading
import time
import re
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs

# 存储部署任务进度
deploy_tasks = {}
task_lock = threading.Lock()

def gen_task_id():
    import random, string
    return ''.join(random.choices(string.ascii_lowercase + string.digits, k=8))

class Handler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        pass  # 静默日志

    def send_json(self, code, data):
        body = json.dumps(data, ensure_ascii=False).encode('utf-8')
        self.send_response(code)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', len(body))
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET, POST, DELETE, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type')
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET, POST, DELETE, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type')
        self.end_headers()

    def read_body(self):
        length = int(self.headers.get('Content-Length', 0))
        return self.rfile.read(length).decode('utf-8') if length else ''

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path
        qs = parse_qs(parsed.query)

        # 健康检查
        if path == '/api/health':
            self.send_json(200, {'status': 'ok', 'time': time.time()})

        # 查询部署任务状态
        elif path == '/api/compose/status':
            task_id = qs.get('id', [''])[0]
            with task_lock:
                task = deploy_tasks.get(task_id)
            if task:
                self.send_json(200, task)
            else:
                self.send_json(404, {'error': '任务不存在'})

        # 列出已部署的 compose 项目
        elif path == '/api/compose/list':
            try:
                result = subprocess.run(
                    ['docker', 'compose', 'ls', '--format', 'json'],
                    capture_output=True, text=True, timeout=10
                )
                try:
                    data = json.loads(result.stdout) if result.stdout.strip() else []
                except:
                    data = []
                self.send_json(200, {'projects': data})
            except Exception as e:
                self.send_json(500, {'error': str(e)})

        else:
            self.send_json(404, {'error': 'not found'})

    def do_POST(self):
        parsed = urlparse(self.path)
        path = parsed.path
        body_raw = self.read_body()

        try:
            body = json.loads(body_raw) if body_raw else {}
        except:
            self.send_json(400, {'error': '请求体格式错误'})
            return

        # 部署 Compose（异步）
        if path == '/api/compose/deploy':
            compose_content = body.get('compose', '')
            project_name = body.get('project', '').strip()
            pull_first = body.get('pull', True)

            if not compose_content:
                self.send_json(400, {'error': 'compose 内容不能为空'})
                return

            # 校验 project name
            if project_name and not re.match(r'^[a-zA-Z0-9_-]+$', project_name):
                self.send_json(400, {'error': '项目名只能包含字母、数字、下划线和连字符'})
                return

            task_id = gen_task_id()
            with task_lock:
                deploy_tasks[task_id] = {
                    'id': task_id,
                    'status': 'running',
                    'logs': [],
                    'started_at': time.time(),
                }

            # 异步执行
            t = threading.Thread(
                target=run_compose_deploy,
                args=(task_id, compose_content, project_name, pull_first),
                daemon=True
            )
            t.start()

            self.send_json(202, {'task_id': task_id, 'message': '部署任务已启动'})

        # 停止/删除 compose 项目
        elif path == '/api/compose/down':
            project_name = body.get('project', '').strip()
            remove_volumes = body.get('volumes', False)

            if not project_name:
                self.send_json(400, {'error': '项目名不能为空'})
                return

            task_id = gen_task_id()
            with task_lock:
                deploy_tasks[task_id] = {
                    'id': task_id,
                    'status': 'running',
                    'logs': [],
                    'started_at': time.time(),
                }

            t = threading.Thread(
                target=run_compose_down,
                args=(task_id, project_name, remove_volumes),
                daemon=True
            )
            t.start()

            self.send_json(202, {'task_id': task_id, 'message': '停止任务已启动'})

        # 验证 compose 格式
        elif path == '/api/compose/validate':
            compose_content = body.get('compose', '')
            if not compose_content:
                self.send_json(400, {'error': 'compose 内容不能为空'})
                return

            tmpdir = tempfile.mkdtemp()
            try:
                compose_path = os.path.join(tmpdir, 'docker-compose.yml')
                with open(compose_path, 'w', encoding='utf-8') as f:
                    f.write(compose_content)

                result = subprocess.run(
                    ['docker', 'compose', '-f', compose_path, 'config'],
                    capture_output=True, text=True, timeout=15
                )
                if result.returncode == 0:
                    self.send_json(200, {'valid': True, 'message': '格式正确'})
                else:
                    self.send_json(200, {'valid': False, 'error': result.stderr.strip()})
            except Exception as e:
                self.send_json(500, {'error': str(e)})
            finally:
                shutil.rmtree(tmpdir, ignore_errors=True)

        else:
            self.send_json(404, {'error': 'not found'})


def run_compose_deploy(task_id, compose_content, project_name, pull_first):
    """在临时目录中执行 docker compose up"""
    tmpdir = tempfile.mkdtemp(prefix='docker_mgr_')
    try:
        compose_path = os.path.join(tmpdir, 'docker-compose.yml')
        with open(compose_path, 'w', encoding='utf-8') as f:
            f.write(compose_content)

        def append_log(line, level='info'):
            with task_lock:
                deploy_tasks[task_id]['logs'].append({'t': time.time(), 'msg': line, 'level': level})

        append_log('📁 创建临时目录: ' + tmpdir)

        # 构建命令
        cmd = ['docker', 'compose', '-f', compose_path]
        if project_name:
            cmd += ['-p', project_name]
        
        if pull_first:
            append_log('📥 拉取最新镜像...')
            pull_result = subprocess.run(
                cmd + ['pull'],
                capture_output=True, text=True, timeout=300
            )
            for line in (pull_result.stdout + pull_result.stderr).splitlines():
                if line.strip():
                    append_log(line)

        append_log('🚀 启动容器...')
        up_result = subprocess.run(
            cmd + ['up', '-d', '--remove-orphans'],
            capture_output=True, text=True, timeout=300
        )

        for line in (up_result.stdout + up_result.stderr).splitlines():
            if line.strip():
                level = 'error' if 'error' in line.lower() else 'warn' if 'warn' in line.lower() else 'info'
                append_log(line, level)

        if up_result.returncode == 0:
            append_log('✅ 部署成功！', 'success')
            with task_lock:
                deploy_tasks[task_id]['status'] = 'success'
        else:
            append_log('❌ 部署失败，退出码: ' + str(up_result.returncode), 'error')
            with task_lock:
                deploy_tasks[task_id]['status'] = 'failed'

    except subprocess.TimeoutExpired:
        with task_lock:
            deploy_tasks[task_id]['status'] = 'failed'
            deploy_tasks[task_id]['logs'].append({'t': time.time(), 'msg': '❌ 超时（5分钟），任务终止', 'level': 'error'})
    except Exception as e:
        with task_lock:
            deploy_tasks[task_id]['status'] = 'failed'
            deploy_tasks[task_id]['logs'].append({'t': time.time(), 'msg': f'❌ 异常: {e}', 'level': 'error'})
    finally:
        # 清理临时目录（延迟，防止 compose 文件被删导致问题）
        threading.Timer(60, lambda: shutil.rmtree(tmpdir, ignore_errors=True)).start()


def run_compose_down(task_id, project_name, remove_volumes):
    """停止并移除 compose 项目"""
    try:
        def append_log(line, level='info'):
            with task_lock:
                deploy_tasks[task_id]['logs'].append({'t': time.time(), 'msg': line, 'level': level})

        cmd = ['docker', 'compose', '-p', project_name, 'down']
        if remove_volumes:
            cmd.append('-v')

        append_log(f'🛑 正在停止项目 {project_name}...')
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)

        for line in (result.stdout + result.stderr).splitlines():
            if line.strip():
                append_log(line)

        if result.returncode == 0:
            append_log('✅ 项目已停止并移除', 'success')
            with task_lock:
                deploy_tasks[task_id]['status'] = 'success'
        else:
            append_log('❌ 操作失败，退出码: ' + str(result.returncode), 'error')
            with task_lock:
                deploy_tasks[task_id]['status'] = 'failed'
    except Exception as e:
        with task_lock:
            deploy_tasks[task_id]['status'] = 'failed'
            deploy_tasks[task_id]['logs'].append({'t': time.time(), 'msg': f'❌ 异常: {e}', 'level': 'error'})


def main():
    port = int(os.environ.get('BACKEND_PORT', '8081'))
    server = HTTPServer(('0.0.0.0', port), Handler)
    print(f'Docker Manager Backend listening on :{port}')
    server.serve_forever()


if __name__ == '__main__':
    main()
