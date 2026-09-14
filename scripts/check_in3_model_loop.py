"""Model-in-the-loop IN3 verification: name submit, cross-turn find, report read,
and the broker step boundary.

Requires the isolated stack to be running already (this script does not start or
stop services):

  # stock broker, worker off, demo state dir, port 8766
  D:\\Stock_Agent_Workspace\\.venv-paper\\Scripts\\python.exe scripts/run_paper.py \
      --state-dir data/bridge-check-in22 --no-worker --port 8766
  # chat BFF, port 9510
  .\\.conda\\python.exe scripts/run_in1.py

The seeded demo tasks must belong to the conversation under test
(`scripts/seed_bridge_demo.py --conversation-id <id>`); pass --conversation-id or
let this script create a fresh conversation first.

This spends real model calls and is counted by the model-call ledger, so it refuses to
run without --yes.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
for stream in (sys.stdout, sys.stderr):
    if hasattr(stream, 'reconfigure'):
        stream.reconfigure(encoding='utf-8', errors='replace')
BFF = 'http://127.0.0.1:9510'
BROKER = 'http://127.0.0.1:8766'
MARKER = 'SYNTHETIC_DEMO_NOT_FOR_CLOUD'
LEDGER = ROOT / 'data' / 'model-calls.sqlite'
AUDIT = ROOT / 'data' / 'logs' / 'audit.jsonl'
MODEL = 'qwen:qwen3.5-flash'

BOUNDARY_REQUEST = (
    '请依次执行下面每一步，每一步都要单独调用工具，不要跳过：'
    '1) 查看账户；2) 查找研究任务；3) 读取 600150 的报告；4) 查找 600519 的研究；'
    '5) 再查看账户；6) 再查找研究任务；7) 再读取 600150 的报告；8) 再查看账户。'
    '全部做完后汇总说明。'
)

TURNS = [
    ('name-reuse', '帮我研究一下中国船舶。'),
    ('name-new', '再帮我研究一下中国石油。'),
    ('cross-turn-find', '本会话现在有哪些研究任务？逐个说状态。'),
    ('report-read', '读一下 600150 的那份研究报告，用三句话总结要点。'),
    ('cancel-resume', '把刚才提交的中国石油（601857）那个研究任务停掉，然后尝试恢复它，分别说明各自的结果。'),
    ('step-boundary', BOUNDARY_REQUEST),
]


def _ledger_count() -> int:
    if not LEDGER.exists():
        return 0
    connection = sqlite3.connect(f'file:{LEDGER}?mode=ro', uri=True)
    try:
        return connection.execute('SELECT COUNT(*) FROM calls').fetchone()[0]
    finally:
        connection.close()


def _audit_tail(since: str) -> list[dict]:
    if not AUDIT.exists():
        return []
    rows = []
    for line in AUDIT.read_text(encoding='utf-8').splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            continue
        if str(item.get('ts', '')) >= since:
            rows.append(item)
    return rows


def _create_conversation(client: httpx.Client, model: str) -> str:
    response = client.post(f'{BFF}/api/conversations', json={'title': 'IN3 模型在环', 'model': model})
    response.raise_for_status()
    return response.json()['id']


def _run_turn(client: httpx.Client, conversation_id: str, key: str, message: str, model: str) -> dict:
    payload = {'message': message, 'conversation_id': conversation_id, 'model': model,
               'request_id': f'in3-{key}-{int(time.time())}'}
    tools: list[dict] = []
    local_results: list[str] = []
    assistant = ''
    errors: list[str] = []
    started = time.time()
    with client.stream('POST', f'{BFF}/api/chat/stream', json=payload) as response:
        if response.status_code != 200:
            body = response.read().decode('utf-8', 'replace')
            raise RuntimeError(f'HTTP {response.status_code}: {body[:400]}')
        for line in response.iter_lines():
            if not line.strip():
                continue
            event = json.loads(line)
            kind = event.get('event')
            data = event.get('data') or {}
            if kind == 'tool.completed':
                tools.append({'name': data.get('name'), 'status': data.get('status'),
                              'params': data.get('params'), 'result': data.get('result'),
                              'detail': (data.get('detail') or '')[:400]})
            elif kind == 'tool.local_result':
                local_results.append(data.get('local_result') or '')
            elif kind == 'assistant.completed':
                assistant = data.get('text') or assistant
            elif kind == 'error':
                errors.append(data.get('message') or '')
    return {'key': key, 'message': message, 'tools': tools, 'local_results': local_results,
            'assistant': assistant, 'errors': errors, 'seconds': round(time.time() - started, 1)}


def _stock_calls(turn: dict) -> list[dict]:
    return [tool for tool in turn['tools'] if str(tool.get('name', '')).startswith('stock_')]


def _boundary_hit(turn: dict) -> bool:
    for tool in _stock_calls(turn):
        blob = json.dumps({'r': tool.get('result'), 'd': tool.get('detail')}, ensure_ascii=False)
        if '上限' in blob or 'STEP_LIMIT' in blob or 'TIME_LIMIT' in blob:
            return True
    return False


def _check(turn: dict, checks: list[tuple[str, bool, str]]) -> None:
    for label, ok, note in checks:
        turn.setdefault('checks', []).append({'label': label, 'ok': bool(ok), 'note': note})


def evaluate(turn: dict) -> None:
    key, tools, text = turn['key'], turn['tools'], turn['assistant']
    stock = _stock_calls(turn)
    names = [tool['name'] for tool in tools]
    if key in ('name-reuse', 'name-new'):
        submit = [tool for tool in stock if tool['name'] == 'stock_research_submit']
        params = (submit[0].get('params') or {}) if submit else {}
        blob = json.dumps(submit[0].get('result'), ensure_ascii=False) if submit else ''
        mapped = '600150' if key == 'name-reuse' else '601857'
        checks = [
            ('调用 stock_research_submit', bool(submit), f'tools={names}'),
            ('按名称提交（带 name、不带 symbol）', 'name' in params and 'symbol' not in params, f'params={params}'),
            (f'名称解析为 {mapped}', mapped in blob, blob[:160]),
        ]
        if key == 'name-new':
            checks.append(('新任务返回受理回执', any(word in blob for word in ('QUEUED', 'CREATED')), blob[:160]))
            checks.append(('没有冒充完成', ('排队' in text or '受理' in text or '稍后' in text)
                           and '已完成研究' not in text, text[:160]))
        else:
            checks.append(('已有任务被复用', any(word in blob for word in ('REUSED', 'SUCCEEDED')), blob[:160]))
        _check(turn, checks)
    elif key == 'cross-turn-find':
        found = [tool for tool in stock if tool['name'] == 'stock_research_find']
        _check(turn, [
            ('跨轮调用 stock_research_find', bool(found), f'tools={names}'),
            ('结果含本会话任务', any(word in text for word in ('600150', '中国船舶')), text[:160]),
        ])
    elif key == 'report-read':
        read = [tool for tool in stock if tool['name'] == 'stock_report_read']
        _check(turn, [
            ('调用 stock_report_read', bool(read), f'tools={names}'),
            ('完整报告作为本地附件交付', bool(turn['local_results']), f'count={len(turn["local_results"])}'),
            ('本地附件含合成标记', any(MARKER in item for item in turn['local_results']), ''),
            ('标记未进入模型回答', MARKER not in text, ''),
        ])
    elif key == 'cancel-resume':
        cancel = [tool for tool in stock if tool['name'] == 'stock_research_cancel']
        resume = [tool for tool in stock if tool['name'] == 'stock_research_resume']
        _check(turn, [
            ('调用 stock_research_cancel', bool(cancel), f'tools={names}'),
            ('调用 stock_research_resume', bool(resume), f'tools={names}'),
            ('说明停止与恢复结果', bool(text.strip()) and ('停止' in text or '取消' in text), text[:160]),
        ])
    elif key == 'step-boundary':
        _check(turn, [
            ('本轮回合正常结束（无流错误）', not turn['errors'], f'errors={turn["errors"][:2]}'),
            ('触发步数/时间边界', _boundary_hit(turn), f'stock_calls={len(stock)}'),
            ('仍交付最终回答', bool(text.strip()), text[:160]),
            ('未出现 409 失步', not any('409' in json.dumps(tool, ensure_ascii=False) for tool in stock), ''),
        ])


def _write_report(path: Path, started_at: str, conversation_id: str, model: str,
                  before: int, after: int, turns: list[dict]) -> None:
    report = {'started_at': started_at, 'conversation_id': conversation_id, 'model': model,
              'ledger_before': before, 'ledger_after': after,
              'model_calls_spent': after - before, 'turns': turns,
              'audit': [row for row in _audit_tail(started_at)
                        if str(row.get('event', '')).startswith(('stock_', 'tool_loop'))]}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--yes', action='store_true', help='确认消耗真实模型调用')
    parser.add_argument('--conversation-id', help='已有会话 id（需已为其播种演示任务）；默认新建会话')
    parser.add_argument('--model', default=MODEL)
    parser.add_argument('--only', help='只运行指定回合，逗号分隔（如 cancel-resume）')
    parser.add_argument('--out', type=Path, default=ROOT / 'data' / 'in3-model-loop.json')
    args = parser.parse_args()
    model = args.model
    if not args.yes:
        print('需要 --yes 确认消耗真实模型调用', file=sys.stderr)
        return 2

    started_at = datetime.now(timezone.utc).isoformat()
    before = _ledger_count()
    conversation_id = args.conversation_id or ''
    client = httpx.Client(timeout=httpx.Timeout(600.0, connect=10.0))
    try:
        health = client.get(f'{BFF}/health').json()
        broker = client.get(f'{BROKER}/health').json()
        print(f"BFF {health} broker worker={broker.get('worker')} version={broker.get('version')}")
        conversation_id = args.conversation_id or _create_conversation(client, model)
        print(f'conversation={conversation_id} model={model} ledger_before={before}')
        turns = []
        selected = set(args.only.split(',')) if args.only else None
        for key, message in TURNS:
            if selected and key not in selected:
                continue
            print(f'\n=== {key} ===')
            turn = _run_turn(client, conversation_id, key, message, model)
            evaluate(turn)
            turns.append(turn)
            _write_report(args.out, started_at, conversation_id, model, before, _ledger_count(), turns)
            for tool in _stock_calls(turn):
                print(f"  tool {tool['name']} {tool['status']} params={tool['params']}")
            for item in turn.get('checks', []):
                print(f"  [{'PASS' if item['ok'] else 'FAIL'}] {item['label']} {item['note']}")
            print(f"  assistant: {turn['assistant'][:200]!r}")
    finally:
        client.close()

    after = _ledger_count()
    _write_report(args.out, started_at, conversation_id, model, before, after, turns)
    failed = [item['label'] for turn in turns for item in turn.get('checks', []) if not item['ok']]
    print(f'\n模型调用 {before} -> {after}（本轮消耗 {after - before}）')
    print(f'报告写入 {args.out}')
    if failed:
        print('未通过：' + '; '.join(failed))
        return 1
    print('全部检查通过')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
