"""Prepare and execute real Harness holdout tasks through the original chat API."""
import argparse
import hashlib
from html.parser import HTMLParser
import json
from pathlib import Path
import shutil
import sys
import time
import uuid

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from core.ui_session import session_token

BASE = 'http://127.0.0.1:9510'
WORKSPACES = {'cloud': ('fd3554dd13fe', 'deepseek:deepseek-flash'),
              'local': ('e6ec6edca523', 'ollama:qwen3.8:27b'),
              'gemma': ('111917e44f26', 'ollama:gemma4:26b')}
LIVE = {'RUNNING', 'PAUSING', 'PAUSED', 'STOPPING'}


class Text(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts = []
        self.skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in ('script', 'style'):
            self.skip += 1
        if tag in ('p', 'h1', 'h2', 'h3', 'li', 'tr', 'dt', 'dd'):
            self.parts.append('\n')

    def handle_endtag(self, tag):
        if tag in ('script', 'style'):
            self.skip = max(0, self.skip - 1)
        if tag in ('p', 'h1', 'h2', 'h3', 'li', 'tr', 'dt', 'dd'):
            self.parts.append('\n')

    def handle_data(self, data):
        if not self.skip:
            self.parts.append(data)


def text_of(path):
    parser = Text()
    parser.feed(path.read_text(encoding='utf-8'))
    return '\n'.join(line.strip() for line in ''.join(parser.parts).splitlines() if line.strip())


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def prepare(role, variant):
    workspace, _ = WORKSPACES[role]
    root = ROOT / 'data/workspace/.harness' / workspace
    if not root.is_dir():
        raise RuntimeError('验收工作区尚未由原入口创建：' + workspace)
    tasks = []

    def case(capability, name, prompt, files):
        directory = root / 'cases' / capability / f'{name}-{variant}'
        directory.mkdir(parents=True, exist_ok=False)
        for source, target in files.items():
            target_path = directory / target
            target_path.parent.mkdir(parents=True, exist_ok=True)
            if isinstance(source, str) and source.startswith('text:'):
                target_path.write_text(source[5:], encoding='utf-8')
            else:
                shutil.copyfile(ROOT / source, target_path)
        inputs = [{'path': '/workspace/' + str(path.relative_to(root)).replace('\\', '/'),
                   'sha256': sha(path)} for path in sorted(directory.rglob('*')) if path.is_file()]
        tasks.append({'capability': capability, 'task': name, 'prompt': prompt, 'inputs': inputs})

    case('meeting-notes', 'm1',
         '把 /workspace/cases/meeting-notes/m1-{v}/record.md 整理成一份能交接的会议纪要。区分已决定、提议与待确认，待办保留负责人和期限；原文没给的不要猜。保存到同目录 minutes.md，回读核对后告诉我文件位置与主要待办。'.format(v=variant),
         {'docs/记录/2026-10-15-固定端口启动恢复.md': 'record.md'})
    case('meeting-notes', 'm2',
         '请阅读 /workspace/cases/meeting-notes/m2-{v}/record.md，整理这次讨论的决议、后续动作与未决问题。不要把建议或待验事项写成已完成，也不要补造责任人。将结果保存到同目录 minutes.md，实际回读后再交付。'.format(v=variant),
         {'docs/记录/2026-10-09-IN4.20-公开线索与只读会话补修.md': 'record.md'})
    left = 'order,amount,qty\nA100,100.10,2\nA200,250.00,1\nA300,75.50,3\nA400,19.99,1\nA500,10.00,1\nA700,5.00,1\n'
    right = 'order,amount,qty\nA100,100.10,2\nA200,250.05,1\nA300,75.50,3\nA400,19.99,1\nA400,19.99,1\nA600,10.00,1\nA700,NA,1\n'
    case('table-reconcile', 't1',
         '请核对 /workspace/cases/table-reconcile/t1-{v}/left.csv 与 right.csv，按 order 关联，金额单位为元、数量单位为件。分别列出值差、缺行、重复编号与无法比较的条目，缺行不要当零。结果存为同目录 reconciliation.json，回读后用中文说明。'.format(v=variant),
         {'text:' + left: 'left.csv', 'text:' + right: 'right.csv'})
    left2 = 'statement_id\trevenue\tcash\nS001\t375871341.81\t92303380.99\nS002\t3377319888.05\t3488872853.48\nS003\t100.00\t7.5\nS004\t6.00\t1\n'
    right2 = 'statement_id\trevenue\tcash\nS001\t375871341.81\t92303380.99\nS002\t3377319888.05\t3488872853.49\nS003\t100.00\t7.5\nS003\t100.00\t7.5\nS004\tNA\t1\nS005\t12.00\t2\n'
    case('table-reconcile', 't2',
         '请比较 /workspace/cases/table-reconcile/t2-{v}/left.tsv 和 right.tsv 的 revenue、cash，按 statement_id 对齐，单位都是元。精确找出差额、缺失、重复键和非数值，不要用近似值掩盖。把可复核结果保存为同目录 reconciliation.json，回读确认后总结。'.format(v=variant),
         {'text:' + left2: 'left.tsv', 'text:' + right2: 'right.tsv'})
    case('document-compare', 'c1',
         '请比较 /workspace/cases/document-compare/c1-{v}/local.md 与官网当前页面 https://docs.python.org/3/whatsnew/3.13.html 中关于自由线程和 JIT 的说明。区分本地保存的旧版本与网上当前信息，列出适用范围、条件和仍待核对处，保留可定位依据。保存到同目录 comparison.md 并回读。'.format(v=variant),
         {'data/tmp/harness-public/python-3.12.txt': 'local.md'})
    case('document-compare', 'c2',
         '请对照 /workspace/cases/document-compare/c2-{v}/local.md 和 https://docs.python.org/3/whatsnew/3.14.html 当前官网说明，关注模板字符串与延迟注解。不要把本地旧稿当成最新事实，也不要把未提及当成反对。将差异、版本和来源位置保存为同目录 comparison.md，回读确认。'.format(v=variant),
         {'data/tmp/harness-public/python-3.13.txt': 'local.md'})
    case('document-index', 'i1',
         '请只在 /workspace/cases/document-index/i1-{v}/ 的资料里定位自由线程与 JIT 的说明，回读对应原文后说明各在哪份文件、哪些段落。不要凭文件名或摘要直接下结论。'.format(v=variant),
         {'data/tmp/harness-public/python-3.12.txt': 'python-3.12.md',
          'data/tmp/harness-public/python-3.13.txt': 'python-3.13.md'})
    case('document-index', 'i2',
         '请只在 /workspace/cases/document-index/i2-{v}/ 的资料里找 522,186,700 和公告编号 2026-026，回读原文件后告诉我它们出现在哪份公告、什么位置；另外列出该文件里能确认的证券代码。不要凭记忆补公司信息。'.format(v=variant),
         {'verify/lawsuit_20260715.pdf': 'lawsuit.pdf',
          'data/tmp/harness-public/python-3.12.txt': 'other.md'})
    case('document-batch', 'o1',
         '请处理 /workspace/cases/document-batch/o1-{v}/chinese-scan.jpg 和 english-scan.tif，需要能搜索到内容，同时保留原件。请等待处理结束，回读实际生成的文本，再报告各文件结果和成果位置；不要只提交任务就结束。'.format(v=variant),
         {'data/tmp/harness-public/paddle-11.jpg': 'chinese-scan.jpg',
          'data/tmp/harness-public/english-scan.tif': 'english-scan.tif'})
    case('document-batch', 'o2',
         '请识别 /workspace/cases/document-batch/o2-{v}/chinese-label.jpg 和 broken.pdf，保留原件。完成后分别说明哪份成功、哪份失败以及原因，并回读可搜索成果；不要把部分成功说成全部成功，也不要只提交任务。'.format(v=variant),
         {'data/tmp/harness-public/scene-2.jpg': 'chinese-label.jpg',
          'text:%PDF-1.4\n deliberately broken for per-file failure\n': 'broken.pdf'})
    case('ordinary-chat', 'n1', '为什么天空看起来是蓝色的？请用小学生能懂的大白话解释，不用查资料。', {})
    case('ordinary-chat', 'n2', '帮我想一句普通的周末祝福，送给喜欢安静休息的朋友。不用联网或处理文件。', {})
    manifest = root / f'holdout-{variant}.json'
    manifest.write_text(json.dumps(tasks, ensure_ascii=False, indent=2), encoding='utf-8')
    evidence = ROOT / 'data/private/harness-holdout' / role / variant
    evidence.mkdir(parents=True, exist_ok=True)
    (evidence / 'inputs.json').write_text(json.dumps(tasks, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({'role': role, 'workspace': workspace, 'tasks': len(tasks), 'evidence': str(evidence)}, ensure_ascii=False))


def scrub(value):
    if isinstance(value, dict):
        return {key: scrub(item) for key, item in value.items() if key not in ('reasoning_content', 'thinking')}
    if isinstance(value, list):
        return [scrub(item) for item in value]
    return value


def structure(task, messages, inputs, root, ocr_verifications=None):
    rows = [row for row in messages if row.get('role') == 'tool']
    def succeeded(row):
        result = row.get('tool_result') or {}
        return (not result.get('error') and result.get('success') is not False
                and result.get('exit_code', 0) == 0
                and result.get('status') not in ('failed', 'FAILED', 'ERROR'))
    names = {row.get('tool_name') for row in rows if succeeded(row)}
    if task['capability'] == 'ordinary-chat':
        capabilities = {'skill_list', 'skill_info', 'documents_index', 'documents_find', 'file_fingerprint',
                        'documents_batch', 'documents_batch_status', 'documents_batch_cancel',
                        'web_search', 'web_fetch', 'research_begin', 'stock_research_submit'}
        used = {row.get('tool_name') for row in rows}
        return {'tools': sorted(name for name in names if name), 'originals_unchanged': True,
                'no_new_capability_used': not capabilities.intersection(used)
                    and not any(isinstance(name, str) and name.startswith('stock_') for name in used),
                'answer_nonempty': any(row.get('role') == 'assistant' and row.get('content', '').strip()
                                       and not row['content'].startswith('本轮未完成') for row in messages)}
    required = {
        'meeting-notes': {'skill_info'},
        'table-reconcile': {'skill_info'},
        'document-compare': {'skill_info'},
        'document-index': {'documents_index', 'documents_find'},
        'document-batch': {'documents_batch', 'documents_batch_status'},
    }[task['capability']]
    reads = []
    for row in rows:
        if row.get('tool_name') != 'file_read':
            continue
        params = row.get('params') or {}
        result = row.get('tool_result') or {}
        reads.append({'path': params.get('path'), 'sha256': result.get('sha256'),
                      'range': result.get('range'), 'error': result.get('error')})
    originals = all(sha(root / item['path'].removeprefix('/workspace/')) == item['sha256'] for item in inputs)
    script = any(row.get('tool_name') in ('code_exec', 'shell_exec')
                 and 'reconcile.py' in json.dumps(row.get('params') or {})
                 and '.skill_registry/versions/' in json.dumps(row.get('params') or {}) for row in rows)

    def path_read(path, *, allow_short=False):
        local = root / path.removeprefix('/workspace/')
        if not local.is_file():
            return False
        try:
            text = '' if local.suffix.lower() == '.pdf' else local.read_text(encoding='utf-8-sig')
        except (OSError, UnicodeError, ValueError, RuntimeError):
            return False
        normalize = lambda value: ' '.join(str(value).split())
        evidence = [normalize(line) for line in text.splitlines()
                    if allow_short and normalize(line) or len(normalize(line)) >= 40]
        for row in rows:
            if not succeeded(row):
                continue
            name, params, result = row.get('tool_name'), row.get('params') or {}, row.get('tool_result') or {}
            if name == 'file_read' and params.get('path') == path and result.get('content'):
                return True
            if name in ('code_exec', 'shell_exec'):
                command = json.dumps(params)
                directory, basename = path.rsplit('/', 1)
                referenced = (path in command or basename in command and
                              (f'cd {directory} ' in command or f'cd "{directory}" ' in command
                               or f"cd '{directory}' " in command))
                if not referenced:
                    continue
                if 'reconcile.py' in command and '.skill_registry/versions/' in command:
                    return True
                output = normalize(result.get('stdout', ''))
                if text and (normalize(text) in output or any(line in output for line in evidence)):
                    return True
        return False

    expected_name = {'meeting-notes': 'minutes.md', 'table-reconcile': 'reconciliation.json',
                     'document-compare': 'comparison.md'}.get(task['capability'])
    expected = (inputs[0]['path'].rsplit('/', 1)[0] + '/' + expected_name) if expected_name else ''
    reread = bool(expected and path_read(expected))
    source_reread = all(path_read(item['path']) for item in inputs) if task['capability'] != 'document-batch' else None
    text_reads = [row for row in reads if (row['path'] or '').endswith('.txt')
                  and '/reports/document-batch/' in row['path']]
    batch = next(((row.get('tool_result') or {}) for row in reversed(rows)
                  if row.get('tool_name') == 'documents_batch_status' and succeeded(row)
                  and (row.get('tool_result') or {}).get('files')), {})
    ocr = []
    for item in batch.get('files', []):
        expected_failure = task['task'] == 'o2' and item.get('source', '').endswith('/broken.pdf')
        pdf = item.get('pdf') or ''
        pdf_virtual = pdf if pdf.startswith('/workspace/') else '/workspace/' + pdf.lstrip('/')
        supplied_text = item.get('text') or ''
        text_virtual = supplied_text if supplied_text.startswith('/workspace/') else '/workspace/' + supplied_text.lstrip('/')
        verified = (ocr_verifications or {}).get(pdf_virtual, {})
        ocr.append({'source': item.get('source'), 'state': item.get('state'),
                    'expected_failure': expected_failure,
                    'source_fingerprint_match': next((row['sha256'] == item.get('source_sha256') for row in inputs
                        if row['path'] == item.get('source')), False),
                    'text_read': path_read(text_virtual, allow_short=True) if item.get('state') == 'SUCCEEDED' else None,
                    'pdf_text_characters': verified.get('characters', 0)})
    expected_state = 'PARTIAL' if task['task'] == 'o2' else 'SUCCEEDED'
    ocr_valid = bool(batch and batch.get('state') == expected_state and len(ocr) == len(inputs)
                     and all(row['source_fingerprint_match'] and
                         (row['expected_failure'] and row['state'] == 'FAILED'
                          or not row['expected_failure'] and row['state'] == 'SUCCEEDED'
                          and row['text_read'] and row['pdf_text_characters'] > 0) for row in ocr))
    return {'tools': sorted(name for name in names if name),
            'required_tools_present': required <= names,
            'missing_tools': sorted(required - names), 'reads': reads,
            'originals_unchanged': originals,
            'fixed_reconcile_script': script if task['capability'] == 'table-reconcile' else None,
            'written_artifact_reread': reread,
            'source_reread': source_reread,
            'ocr_text_outputs_read': text_reads,
            'ocr_receipt_and_text_layers_valid': ocr_valid if task['capability'] == 'document-batch' else None,
            'ocr_files': ocr if task['capability'] == 'document-batch' else [],
            'external_read': any(row.get('tool_name') in ('web_fetch', 'web_search') and succeeded(row) for row in rows)
                             if task['capability'] == 'document-compare' else None}


def verify_ocr_outputs(client, workspace, task, messages):
    if task['capability'] != 'document-batch':
        return {}
    from core.conversation_store import ConversationStore
    from core.workspaces import WorkspaceManager, tool_token
    record = WorkspaceManager(ConversationStore(), ROOT / 'data/workspace').get(workspace)
    batch = next(((row.get('tool_result') or {}) for row in reversed(messages)
                  if row.get('tool_name') == 'documents_batch_status'
                  and (row.get('tool_result') or {}).get('files')), {})
    verified = {}
    for item in batch.get('files', []):
        if item.get('state') != 'SUCCEEDED':
            continue
        supplied = item.get('pdf') or ''
        path = supplied if supplied.startswith('/workspace/') else '/workspace/' + supplied.lstrip('/')
        local = ROOT / 'data/workspace/.harness' / workspace / supplied.removeprefix('/workspace/')
        receipt = {'path': path, 'tool': 'file_read', 'workspace_id': workspace,
                   'artifact_sha256': sha(local) if local.is_file() else None}
        try:
            response = client.post(f'http://127.0.0.1:{record["files_port"]}/tool/file_read',
                headers={'X-Workspace-Token': tool_token(workspace)}, json={'path': path})
            result = response.json()
            text = result.get('content') if isinstance(result.get('content'), str) else ''
            receipt.update(characters=len(text) if not result.get('error') and not result.get('unsupported') else 0,
                           error=result.get('error') or result.get('detail'), preview=text[:300])
        except (httpx.HTTPError, OSError, ValueError) as exc:
            receipt.update(characters=0, error=type(exc).__name__ + ': ' + str(exc))
        verified[path] = receipt
    return verified


def execute(role, variant, only):
    workspace, model = WORKSPACES[role]
    root = ROOT / 'data/workspace/.harness' / workspace
    evidence = ROOT / 'data/private/harness-holdout' / role / variant
    tasks = json.loads((root / f'holdout-{variant}.json').read_text(encoding='utf-8'))
    history_path = evidence / 'runs.json'
    history = json.loads(history_path.read_text(encoding='utf-8')) if history_path.exists() else []
    headers = {'X-Harness-Session': session_token(), 'Origin': BASE}
    timeout = httpx.Timeout(1800, connect=15)
    with httpx.Client(base_url=BASE, headers=headers, timeout=timeout, trust_env=False) as client:
        for task in tasks:
            key = task['capability'] + '/' + task['task']
            if only and task['capability'] not in only and key not in only:
                continue
            previous = next((row for row in history if row['key'] == key), None)
            if previous is not None:
                print('RETAIN', key, previous.get('status'), flush=True)
                continue
            request_id = uuid.uuid4().hex
            entry = {'key': key, 'request_id': request_id, 'status': 'SUBMITTED',
                     'capability': task['capability'], 'task': task['task'], 'model': model,
                     'workspace_id': workspace, 'variant': variant, 'prompt': task['prompt']}
            history.append(entry)
            history_path.write_text(json.dumps(history, ensure_ascii=False, indent=2), encoding='utf-8')
            events = []
            try:
                with client.stream('POST', '/api/chat/stream', json={
                        'workspace_id': workspace, 'message': task['prompt'],
                        'model': model, 'request_id': request_id}) as response:
                    if response.is_error:
                        entry['status'] = 'HTTP_ERROR'
                        entry['error'] = response.read().decode('utf-8', errors='replace')[:3000]
                    else:
                        for line in response.iter_lines():
                            if not line.strip():
                                continue
                            event = json.loads(line)
                            if event.get('run_id') and not entry.get('run_id'):
                                entry['run_id'] = event['run_id']
                                history_path.write_text(json.dumps(history, ensure_ascii=False, indent=2), encoding='utf-8')
                            events.append(scrub(event))
                        entry['status'] = 'STREAM_COMPLETE'
            except (httpx.HTTPError, OSError, ValueError) as exc:
                entry['status'] = 'UNKNOWN' if entry.get('run_id') else 'NOT_STARTED'
                entry['error'] = type(exc).__name__ + ': ' + str(exc)
            if entry.get('run_id'):
                run = client.get('/api/runs/' + entry['run_id'])
                if run.is_success:
                    entry['run'] = run.json()
                if entry.get('run', {}).get('status') in LIVE:
                    after = max((event.get('seq', 0) for event in events), default=0)
                    with client.stream('GET', f'/api/runs/{entry["run_id"]}/events?after_seq={after}') as response:
                        for line in response.iter_lines():
                            if line.strip():
                                events.append(scrub(json.loads(line)))
                    entry['run'] = client.get('/api/runs/' + entry['run_id']).json()
                conversation_id = (entry.get('conversation_id') or entry.get('run', {}).get('conversation_id')
                                   or next((event.get('conversation_id') for event in events if event.get('conversation_id')), None))
                entry['conversation_id'] = conversation_id
                if conversation_id:
                    messages_response = client.get(f'/api/conversations/{conversation_id}/messages')
                    if messages_response.is_success:
                        messages = messages_response.json()
                        verification = verify_ocr_outputs(client, workspace, task, messages)
                        entry['ocr_verifications'] = verification
                        entry['checks'] = structure(task, messages, task['inputs'], root, verification)
                        entry['assistant'] = [row.get('content', '') for row in messages if row.get('role') == 'assistant']
                        entry['tool_calls'] = [row for row in messages if row.get('role') == 'tool']
            entry['events'] = events
            entry['needs_content_review'] = True
            history_path.write_text(json.dumps(history, ensure_ascii=False, indent=2), encoding='utf-8')
            (evidence / (task['capability'] + '-' + task['task'] + '.json')).write_text(
                json.dumps(entry, ensure_ascii=False, indent=2), encoding='utf-8')
            print('RECORDED', key, entry['status'], entry.get('run_id', ''), flush=True)
            time.sleep(0.2)


def skill_lifecycle(role, variant, resume_from=''):
    from core.conversation_store import ConversationStore
    from core.workspaces import WorkspaceManager, tool_token
    workspace, model = WORKSPACES[role]
    root = ROOT / 'data/workspace/.harness' / workspace
    evidence = ROOT / 'data/private/harness-holdout' / role / variant / 'skill-lifecycle.json'
    if evidence.exists():
        raise RuntimeError('保留已有 Skill 验收记录，请使用新变体')
    evidence.parent.mkdir(parents=True, exist_ok=True)
    record = {'role': role, 'workspace_id': workspace, 'model': model, 'variant': variant, 'stages': []}
    headers = {'X-Harness-Session': session_token(), 'Origin': BASE}

    def catalog(client):
        response = client.get(f'/api/workspaces/{workspace}/skills')
        response.raise_for_status()
        rows = response.json()['skills']
        return next(row for row in rows if row['id'] == 'handoff-notes'), rows

    def action(client, name, revision=None):
        response = client.post(f'/api/workspaces/{workspace}/skills/handoff-notes',
                               json={'action': name, 'revision': revision})
        if response.is_error:
            raise RuntimeError(f'Skill {name}: {response.text}')
        return response.json()

    def read_version(client, revision):
        from core.workspaces import tool_token
        workspace_record = WorkspaceManager(ConversationStore(), ROOT / 'data/workspace').get(workspace)
        response = client.post(f'http://127.0.0.1:{workspace_record["runner_port"]}/capabilities/skills/read',
            headers={'X-Workspace-Token': tool_token(workspace)}, json={'name': 'handoff-notes', 'revision': revision})
        response.raise_for_status()
        return response.json()

    def chat(client, prompt):
        events, run_id = [], None
        with client.stream('POST', '/api/chat/stream', json={'workspace_id': workspace, 'model': model,
                'message': prompt, 'request_id': uuid.uuid4().hex}) as response:
            if response.is_error:
                raise RuntimeError('聊天受理失败：' + response.read().decode('utf-8', errors='replace'))
            for line in response.iter_lines():
                if line.strip():
                    event = scrub(json.loads(line))
                    events.append(event)
                    run_id = run_id or event.get('run_id')
        run = client.get('/api/runs/' + run_id).json() if run_id else {}
        if run.get('status') != 'SUCCEEDED':
            raise RuntimeError(f'运行未成功：{run_id} {run.get("error")}')
        cid = run['conversation_id']
        messages = client.get(f'/api/conversations/{cid}/messages').json()
        return {'run': run, 'conversation_id': cid, 'events': events,
                'tools': [scrub(row) for row in messages if row.get('role') == 'tool'],
                'assistant': [row.get('content', '') for row in messages if row.get('role') == 'assistant']}

    def save():
        evidence.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding='utf-8')

    with httpx.Client(base_url=BASE, headers=headers, timeout=httpx.Timeout(1800, connect=20), trust_env=False) as client:
        try:
            if resume_from:
                previous = ROOT / 'data/private/harness-holdout' / role / resume_from / 'skill-lifecycle.json'
                prior = json.loads(previous.read_text(encoding='utf-8'))
                if prior.get('error') != '编辑没有形成新版本':
                    raise RuntimeError('只允许从已确认的权限故障继续，不能重放其他失败')
                revision = next(stage['revision'] for stage in prior['stages'] if stage['stage'] == 'verify_create')
                first = read_version(client, revision)
                _, rows = catalog(client)
                current = next(row for row in rows if row['id'] == 'handoff-notes')
                if 'Permission denied' not in current.get('candidate_error', ''):
                    raise RuntimeError('当前不是待修复的权限故障，不能覆盖该状态')
                record['resume_from'] = resume_from
                workspace_record = WorkspaceManager(ConversationStore(), ROOT / 'data/workspace').get(workspace)
                file_url = f'http://127.0.0.1:{workspace_record["files_port"]}/tool/'
                file_headers = {'X-Workspace-Token': tool_token(workspace)}
                response = client.post(file_url + 'file_read', headers=file_headers,
                    json={'path': '/workspace/skills/handoff-notes/SKILL.md'})
                response.raise_for_status()
                current_file = response.json()
                response = client.post(file_url + 'file_edit', headers=file_headers, json={
                    'path': '/workspace/skills/handoff-notes/SKILL.md',
                    'expected_sha256': current_file['sha256'],
                    'old_text': '负责人或期限缺失时写待确认，不得猜测。',
                    'new_text': '负责人或期限缺失时写待确认，不得猜测。'})
                response.raise_for_status()
                repair = response.json()
                if repair.get('changed') is not False or repair.get('sha256') != current_file['sha256']:
                    raise RuntimeError('权限修复不得改变 Skill 正文或指纹')
                response = client.post(f'http://127.0.0.1:{workspace_record["runner_port"]}/tool/code_exec',
                    headers=file_headers, json={'code': 'from pathlib import Path; import hashlib, json; '
                        'p=Path("/workspace/skills/handoff-notes/SKILL.md"); '
                        'print(json.dumps({"sha256": hashlib.sha256(p.read_bytes()).hexdigest()}))'})
                response.raise_for_status()
                runner_read = response.json()
                if runner_read.get('exit_code') != 0:
                    raise RuntimeError('Runner 仍无法读取修复后的 Skill：' + str(runner_read))
                record['stages'].append({'stage': 'repair_permissions', 'tool': 'file_edit',
                    'changed': repair['changed'], 'source_sha256': current_file['sha256'],
                    'runner_read_sha256': json.loads(runner_read['stdout'])['sha256'], 'model_execution': False})
            else:
                record['stages'].append({'stage': 'create', **chat(client,
                    '请在 /workspace/skills/handoff-notes/ 新建一个标准 SKILL.md 和 assets/minutes.md。'
                    '它用于把讨论记录整理成可交接的决议、提议和待办，并保留原文位置。'
                    '不要声明新权限，不要修改其他目录或已有技能，不安装依赖，不执行其中代码。'
                    '名称使用 handoff-notes，正文写清交付前回读。完成后实际回读这两个新文件并报告位置。')})
                created, _ = catalog(client)
                revision = created['active']['revision']
                if not revision or not created['enabled']:
                    raise RuntimeError('新 Skill 未激活')
                first = read_version(client, revision)
                if len(first.get('body', '')) < 40 or not first.get('assets'):
                    raise RuntimeError('新 Skill 正文或模板缺失')
                record['stages'].append({'stage': 'verify_create', 'revision': revision,
                    'path': first['path'], 'assets': first['assets']})
                save()
                record['stages'].append({'stage': 'edit', **chat(client,
                    '请编辑 /workspace/skills/handoff-notes/SKILL.md，新增明确规则：'
                    '“负责人或期限缺失时写待确认，不得猜测。”使用实际的文件编辑工具，保留原其他内容。'
                    '完成后回读该文件。不要修改其他文件，不要执行安装。')})
            updated, _ = catalog(client)
            new_revision = updated['active']['revision']
            if new_revision == revision:
                raise RuntimeError('编辑没有形成新版本')
            second = read_version(client, new_revision)
            if '负责人或期限缺失时写待确认' not in second.get('body', ''):
                raise RuntimeError('编辑后的固定版本未包含指定规则')
            if not any(row.get('revision') == revision for row in updated['history']):
                raise RuntimeError('编辑前版本未保留')
            record['stages'].append({'stage': 'verify_edit', 'before': revision, 'after': new_revision,
                'before_path': first['path'], 'after_path': second['path'], 'history_preserved': True})
            save()
            action(client, 'rollback', revision)
            rolled, _ = catalog(client)
            if rolled['active']['revision'] != revision:
                raise RuntimeError('回退版本不匹配')
            if read_version(client, revision)['body'] != first['body']:
                raise RuntimeError('回退后的正文不一致')
            record['stages'].append({'stage': 'rollback', 'revision': revision})
            action(client, 'disable')
            disabled, _ = catalog(client)
            if disabled['enabled']:
                raise RuntimeError('Skill 未停用')
            action(client, 'activate', new_revision)
            restored, _ = catalog(client)
            if restored['active']['revision'] != new_revision or not restored['enabled']:
                raise RuntimeError('Skill 未按指定版本恢复')
            record['stages'].append({'stage': 'disable_and_restore', 'revision': new_revision})
            action(client, 'uninstall')
            uninstalled, _ = catalog(client)
            if not uninstalled.get('uninstalled') or uninstalled['enabled']:
                raise RuntimeError('卸载状态不正确')
            removed = chat(client, '请只读取当前能力目录，随后简短回复已读取。')
            tool_text = json.dumps(removed['tools'], ensure_ascii=False)
            if 'handoff-notes' in tool_text:
                raise RuntimeError('卸载后仍出现在本轮发现目录')
            record['stages'].append({'stage': 'uninstall_discovery', 'run': removed['run']})
            from core.workspaces import tool_token
            workspace_record = WorkspaceManager(ConversationStore(), ROOT / 'data/workspace').get(workspace)
            response = client.post(f'http://127.0.0.1:{workspace_record["files_port"]}/tool/file_delete',
                headers={'X-Workspace-Token': tool_token(workspace)}, json={'path': '/workspace/skills/handoff-notes'})
            response.raise_for_status()
            if (root / 'skills/handoff-notes').exists():
                raise RuntimeError('源目录未移入回收站')
            difference = client.get(f'/api/workspaces/{workspace}/skills/handoff-notes/diff',
                                    params={'before': revision, 'after': new_revision})
            difference.raise_for_status()
            if not any(row.get('change') == 'modified' for row in difference.json().get('files', [])):
                raise RuntimeError('删除源目录后无法核对保留的版本差异')
            _, rows = catalog(client)
            if any(row['id'] == 'handoff-notes' and row.get('enabled') and not row.get('uninstalled') for row in rows):
                raise RuntimeError('卸载后Skill仍处于启用状态')
            record['stages'].append({'stage': 'delete_source_history_retained', 'diff': difference.json()})
            record['status'] = 'COMPLETED'
        except Exception as exc:
            record['status'] = 'FAILED'
            record['error'] = str(exc)
        save()
    print(json.dumps({'output': str(evidence), 'status': record['status'], 'error': record.get('error')}, ensure_ascii=False))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('role', choices=tuple(WORKSPACES))
    parser.add_argument('--variant', default='v1')
    parser.add_argument('--prepare', action='store_true')
    parser.add_argument('--run', action='store_true')
    parser.add_argument('--only', nargs='*', default=[])
    parser.add_argument('--skill-lifecycle', action='store_true')
    parser.add_argument('--resume-from', default='')
    args = parser.parse_args()
    if args.skill_lifecycle:
        skill_lifecycle(args.role, args.variant, args.resume_from)
        return
    if args.prepare:
        prepare(args.role, args.variant)
    if args.run:
        execute(args.role, args.variant, set(args.only))
    if not args.prepare and not args.run:
        parser.error('choose --prepare and/or --run')


if __name__ == '__main__':
    main()
