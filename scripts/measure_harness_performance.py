"""Measure the running product without owning its process or Docker lifecycle."""
import argparse
import gzip
import json
import re
import socket
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core.ui_session import session_token

BASE = 'http://127.0.0.1:9510'
HEADERS = {'X-Harness-Session': session_token(), 'Origin': BASE}


def listening(port):
    with socket.socket() as sock:
        sock.settimeout(.3)
        return sock.connect_ex(('127.0.0.1', port)) == 0


def stop_stack():
    settings_path = ROOT / 'data/private/daily-services.json'
    settings = json.loads(settings_path.read_text(encoding='utf-8')) if settings_path.is_file() else {}
    stock_port = settings.get('port') if settings.get('enabled') else None
    try:
        with httpx.Client(timeout=130, trust_env=False) as client:
            response = client.post(BASE + '/api/admin/shutdown-stack', headers=HEADERS)
            if response.status_code != 200:
                raise RuntimeError(f'原退出接口返回 {response.status_code}: {response.text[:300]}')
    except httpx.ConnectError:
        pass
    deadline = time.monotonic() + 120
    while time.monotonic() < deadline:
        if not listening(9510) and not listening(9511) and (not stock_port or not listening(stock_port)):
            return
        time.sleep(.4)
    raise RuntimeError('原入口未确认退出，未强杀进程')


def process_memory(launcher_pid, stock_port=None):
    path = ROOT / 'data/private/runtime-processes.json'
    identities = json.loads(path.read_text(encoding='utf-8-sig')) if path.is_file() else {}
    if launcher_pid:
        identities['launcher_pid'] = launcher_pid
    required = ['bff_pid', 'launcher_pid']
    if stock_port:
        required += ['stock_pid', 'stock_server_pid']
    missing = [name for name in required if not identities.get(name)]
    if missing:
        raise RuntimeError('缺少原启动器记录的实际进程身份：' + ', '.join(missing))
    command = ('Get-Process -Id ' + ','.join(str(identities[name]) for name in dict.fromkeys(required)) +
               ' | Select-Object Id,WorkingSet64 | ConvertTo-Json -Compress')
    result = subprocess.run(['powershell.exe', '-NoProfile', '-Command', command],
                            capture_output=True, text=True, errors='replace', timeout=30)
    if result.returncode or not result.stdout.strip():
        raise RuntimeError('无法读取原启动器登记的进程；服务可能已退出或未完成启动')
    rows = json.loads(result.stdout)
    rows = [rows] if isinstance(rows, dict) else rows
    by_id = {row['Id']: row.get('WorkingSet64') for row in rows}
    resources = {'bff': 0, 'stock': 0, 'launcher': 0, 'processes': []}
    seen = set()
    for role, name in (('bff', 'bff_pid'), ('stock', 'stock_pid'), ('stock', 'stock_server_pid'),
                       ('launcher', 'launcher_pid')):
        if name not in required:
            continue
        pid = identities[name]
        if pid in seen:
            continue
        seen.add(pid)
        if by_id.get(pid) is None:
            raise RuntimeError(f'无法读取 {role} 的实际进程内存（PID {pid}）')
        resources[role] += by_id[pid]
        resources['processes'].append({'role': role, 'pid': pid, 'rss_bytes': by_id[pid]})
    resources['total_rss_bytes'] = resources['bff'] + resources['stock'] + resources['launcher']
    return resources


def docker_stats():
    result = subprocess.run(['docker', 'stats', '--no-stream', '--format', '{{json .}}'],
                            capture_output=True, text=True, errors='replace', timeout=60)
    if result.returncode:
        raise RuntimeError('无法读取当前容器资源')
    rows = []
    for line in result.stdout.splitlines():
        if not line.strip():
            continue
        value = json.loads(line)
        name = value.get('Name', '')
        scope = ('workspace' if name.startswith(('skill-', 'ws-', 'plugin-'))
                 else 'legacy_shared' if name.startswith('docker-') else 'other')
        rows.append({'name': name, 'scope': scope, 'cpu_percent': value.get('CPUPerc'),
                     'memory_usage': value.get('MemUsage'), 'memory_percent': value.get('MemPerc'),
                     'pids': value.get('PIDs'), 'block_io': value.get('BlockIO'), 'net_io': value.get('NetIO')})
    return rows


def model_and_tool_metrics():
    from math import ceil
    from statistics import median
    models = {}
    path = ROOT / 'data/logs/audit.jsonl'
    if path.is_file():
        with path.open(encoding='utf-8') as source:
            for line in source:
                try:
                    event = json.loads(line)
                except ValueError:
                    continue
                if event.get('event') != 'native_model_request':
                    continue
                name = event.get('model', '')
                row = models.setdefault(name, {'native_requests': 0, 'max_input_chars': 0,
                    'max_message_count': 0, 'max_tool_schema_chars': 0, 'tool_schema_chars_reported': 0})
                row['native_requests'] += 1
                row['max_input_chars'] = max(row['max_input_chars'], event.get('input_chars') or 0)
                row['max_message_count'] = max(row['max_message_count'], event.get('message_count') or 0)
                row['max_tool_schema_chars'] = max(row['max_tool_schema_chars'], event.get('tool_schema_chars') or 0)
                row['tool_schema_chars_reported'] += int('tool_schema_chars' in event)
    latencies = {}
    for role in ('cloud', 'local', 'gemma'):
        for variant in (ROOT / 'data/private/harness-holdout' / role).glob('v*'):
            for path in variant.glob('*.json'):
                if path.name in ('runs.json', 'inputs.json', 'case.json', 'summary.json'):
                    continue
                try:
                    evidence = json.loads(path.read_text(encoding='utf-8'))
                except (OSError, ValueError):
                    continue
                for event in evidence.get('events', []):
                    if event.get('event') != 'tool.completed':
                        continue
                    data = event.get('data', {})
                    elapsed = data.get('elapsed')
                    if isinstance(elapsed, (int, float)):
                        latencies.setdefault(data.get('name'), []).append(elapsed)
    timings = {}
    for name, values in latencies.items():
        values.sort()
        timings[name] = {'samples': len(values), 'median_seconds': median(values),
                         'p95_seconds': values[max(0, ceil(len(values) * .95) - 1)]}
    return {'models': models, 'tool_latencies': timings,
            'sources': ['data/logs/audit.jsonl', 'data/private/harness-holdout/*/v*/']}


def assets():
    html = (ROOT / 'apps/web/dist/index.html').read_text(encoding='utf-8')
    result = {}
    for kind, pattern in [('main_js', r'<script[^>]+src="([^"]+\.js)"'),
                          ('main_css', r'<link[^>]+href="([^"]+\.css)"')]:
        match = re.search(pattern, html)
        if not match:
            raise RuntimeError('首屏资源缺失：' + kind)
        raw = (ROOT / 'apps/web/dist' / match.group(1).lstrip('/')).read_bytes()
        result[kind] = {'path': match.group(1), 'bytes': len(raw), 'gzip_bytes': len(gzip.compress(raw))}
    return result


def capture(args):
    deadline = time.monotonic() + args.ready_seconds
    with httpx.Client(timeout=30, trust_env=False) as client:
        ready_runs = None
        while True:
            try:
                response = client.get(BASE + '/api/status', headers=HEADERS)
                if response.status_code == 200 and response.json().get('daily_services'):
                    status = response.json()
                    ready_runs = client.get(BASE + '/api/runs', headers=HEADERS)
                    if ready_runs.status_code == 200:
                        break
            except httpx.HTTPError:
                pass
            if time.monotonic() >= deadline:
                raise RuntimeError('原入口未在限定时间就绪')
            time.sleep(.25)
        time.sleep(5)
        runs = ready_runs.json().get('runs', [])
        active = [row['id'] for row in runs if row.get('status') in ('RUNNING', 'PAUSING', 'PAUSED', 'STOPPING')]
        if active:
            raise RuntimeError('存在活跃运行，不执行测量后的退出')
        capabilities = client.get(BASE + '/api/capabilities', headers=HEADERS).json().get('capabilities', [])
        stock = client.get(BASE + '/api/admin/stock-service', headers=HEADERS).json()
        if stock.get('settings', {}).get('enabled') and not stock.get('online'):
            raise RuntimeError('股票后台已启用但鉴权状态不可用，未完成本次测量')
        from core import config
        ollama = client.get(config.OLLAMA_BASE_URL.rstrip('/') + '/api/ps')
        ollama.raise_for_status()
        ollama_models = [{key: row.get(key) for key in ('name', 'model', 'size', 'size_vram',
                          'processor', 'context_length', 'expires_at') if key in row}
                         for row in ollama.json().get('models', [])]
    ready_at = time.time()
    stock_port = stock.get('settings', {}).get('port') if stock.get('online') else None
    resources = (json.loads(args.resources.read_text(encoding='utf-8-sig'))
                 if args.resources else process_memory(args.launcher_pid, stock_port))
    warnings = ['股票服务在线但进程内存未识别'] if stock.get('online') and not resources['stock'] else []
    return {'at': datetime.now(timezone.utc).isoformat(),
            'cold_seconds': ready_at - args.started_at if args.started_at else None,
            'resources': resources, 'assets': assets(), 'warnings': warnings,
            'docker': docker_stats(), 'metrics': model_and_tool_metrics(), 'ollama_models': ollama_models,
            'resource_scope': {'host_rss': '仅原启动器登记进程；与旧基线同口径比较',
                               'docker': '当前运行容器另列，不混入宿主RSS',
                               'ollama': '外部模型服务另列，不混入宿主RSS',
                               'browser': '首屏可见性另验，不归属共享浏览器总内存'},
            'stock_service': {'online': stock.get('online'), 'mode': (stock.get('runtime') or {}).get('mode'),
                              'worker': stock.get('worker')},
            'tool_count': len(status.get('tools', [])),
            'capabilities': [{'id': item['id'], 'lifecycle': item.get('lifecycle')} for item in capabilities]}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path)
    parser.add_argument('--ready-seconds', type=int, default=150)
    parser.add_argument('--resources', type=Path)
    parser.add_argument('--started-at', type=float, default=0)
    parser.add_argument('--launcher-pid', type=int, default=0)
    parser.add_argument('--stop', action='store_true')
    parser.add_argument('--stop-only', action='store_true')
    args = parser.parse_args()
    if args.stop_only:
        stop_stack()
        return
    row = capture(args)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(row, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({'cold_seconds': row['cold_seconds'],
                      'total_rss_bytes': row['resources']['total_rss_bytes'],
                      'output': str(args.output) if args.output else None}))
    if args.stop:
        stop_stack()


if __name__ == '__main__':
    main()
