"""IN4.19 acceptance client for the existing BFF, never an application launcher.

Each invocation starts one explicit scenario or collects its existing run.
It inherits the source conversation's model/workspace and never retries a POST.
"""
import argparse
import hashlib
import json
from pathlib import Path
import re
import sys
import uuid

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from core.ui_session import session_token

BASE = 'http://127.0.0.1:9510'
PROMPTS = {
    'announcement': '请核实万德斯（688178）最近公开披露的中标公告，分析这件事对经营和是否继续研究这家公司意味着什么。请查明公告日期及能查到的后续状态，区分合同总额、履约期、收入、利润和回款，给出依据、反证和观察条件，交付一份易读的HTML研究简报。',
    'earnings': '请依据格力电器（000651）最近已经披露的正式财务报告，分析盈利、经营现金流和分红是否支持继续研究。请核对最新可取得的报告期，区分累计与单季、同比与环比，以及分红预案和实施状态；给出关键依据、反证和观察条件，交付易读的HTML研究简报。',
    'policy': '请研究中国农机报废更新补贴政策最近的公开进展，以及它可能怎样传导到一拖股份（601038）的经营。请区分政策目标、已经执行的措施、实际落地证据和公司直接关系，保留不受益或传导失败的反证，交付易读的HTML研究简报。',
    'comparison': '请比较一拖股份（601038）和潍柴动力（000338），从公开业绩、农机相关业务和估值条件判断哪家更值得继续研究。请使用可比期间，不能比较的指标分列；说明各自优势、反证、估值假设和排除理由，交付易读的HTML研究简报。',
}
SCOPE = '\n只研究公开资料，不操作账户或交易，不领取、恢复或重发任何股票后台任务或旧UNKNOWN。使用本会话模型完成取证、实际读取、必要计算、单独核对和发布。资料不足可交付有边界的成果，不补造事实；最终给出报告卡链接。'


def write(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')


def get(client, url, **kwargs):
    response = client.get(url, **kwargs)
    response.raise_for_status()
    return response.json()


def collect(client, result, output):
    if not result.get('run_id'):
        raise RuntimeError('未保存run_id；禁止自动重发，请先核对原request_id')
    run = get(client, '/api/runs/' + result['run_id'])
    result['run'] = {key: run.get(key) for key in (
        'id', 'status', 'error', 'conversation_id', 'workspace_id', 'model', 'last_seq')}
    messages = get(client, '/api/conversations/' + result['conversation_id'] + '/messages')
    if isinstance(messages, dict):
        messages = messages.get('messages', [])
    # Do not collect thinking/reasoning, credentials or arbitrary tool payloads.
    result['tools'] = [m.get('tool_name') for m in messages if m.get('role') == 'tool']
    result['answers'] = [m.get('content', '') for m in messages if m.get('role') == 'assistant'
                         and m.get('content') and not m.get('tool_calls')]
    paths = sorted(set(re.findall(r'/workspace/reports/chat-research/[a-f0-9]{32}/report\.html',
                                 '\n'.join(result['answers']))))
    result['reports'] = []
    for path in paths:
        response = client.get('/api/workspace/raw', params={'workspace_id': result['workspace_id'], 'path': path})
        response.raise_for_status()
        body = response.content
        filename = output.with_name(output.stem + '-' + path.split('/')[-2] + '.html')
        filename.write_bytes(body)
        result['reports'].append({'path': path, 'sha256': hashlib.sha256(body).hexdigest(),
                                  'bytes': len(body), 'local_copy': str(filename)})
    result.setdefault('content_acceptance', 'PENDING')
    result.setdefault('user_feedback', 'PENDING')
    write(output, result)
    print(json.dumps({k: result.get(k) for k in ('scenario', 'conversation_id', 'run_id', 'run', 'reports')},
                     ensure_ascii=False), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('scenario', choices=PROMPTS)
    parser.add_argument('--source-conversation', required=True)
    parser.add_argument('--variant', default='v1')
    parser.add_argument('--collect', action='store_true')
    parser.add_argument('--question-file', type=Path, help='本次公开自然问题；只选择验收输入，不改变模型、权限或执行入口')
    parser.add_argument('--revision-of', default='', help='原已保存research.json；新建关联修订，不恢复旧运行')
    parser.add_argument('--feedback-file', type=Path, help='本次明确提供的公开资料核对意见；不会修改原稿')
    args = parser.parse_args()
    if not re.fullmatch(r'[a-zA-Z0-9_-]+', args.variant):
        parser.error('variant只能使用字母、数字、下划线或连字符')
    if args.revision_of and not re.fullmatch(r'/workspace/reports/chat-research/[a-f0-9]{32}/research\.json', args.revision_of):
        parser.error('revision-of须为原工作区已保存研究记录')
    question = (args.question_file.read_text(encoding='utf-8').strip()
                if args.question_file and not args.collect else PROMPTS[args.scenario])
    if not question:
        parser.error('自然问题不能为空')
    directory = ROOT / 'data/private/finance-delivery-20261030'
    directory.mkdir(parents=True, exist_ok=True)
    output = directory / (args.scenario + '-' + args.variant + '.json')
    headers = {'X-Harness-Session': session_token(), 'Origin': BASE}
    with httpx.Client(base_url=BASE, headers=headers, timeout=httpx.Timeout(None, connect=15), trust_env=False) as client:
        if args.collect:
            result = json.loads(output.read_text(encoding='utf-8'))
            if result['source_conversation'] != args.source_conversation:
                raise RuntimeError('收集必须指定原来源会话')
            collect(client, result, output)
            return
        if output.exists():
            raise RuntimeError('验收记录已存在；使用--collect读取，禁止自动重发')
        source = get(client, '/api/conversations/' + args.source_conversation)
        workspaces = get(client, '/api/workspaces')
        entries = workspaces.get('workspaces', []) if isinstance(workspaces, dict) else workspaces
        workspace = next(w for w in entries if w['id'] == source['workspace_id'])
        if not source['model'].startswith('ollama:') and (
                not workspace.get('cloud_read') or not workspace.get('cloud_write')):
            raise RuntimeError('原工作区没有既有读写授权；本客户端不扩大权限')
        status = get(client, '/api/admin/stock-service')
        if status.get('worker', {}).get('enabled') or status.get('worker', {}).get('busy'):
            raise RuntimeError('日常worker未暂停，未提交验收')
        result = {'scenario': args.scenario, 'source_conversation': source['id'],
                  'workspace_id': source['workspace_id'], 'model': source['model'],
                  'request_id': uuid.uuid4().hex, 'status': 'PREPARED'}
        response = client.post('/api/conversations', json={'title': 'IN4.19交付收尾 · ' + args.scenario + ' · ' + args.variant,
            'model': source['model'], 'workspace_id': source['workspace_id'],
            'capability_ids': source.get('capability_ids', [])})
        response.raise_for_status()
        result['conversation_id'] = response.json()['id']
        result['status'] = 'SUBMITTING'
        prompt = question + SCOPE
        if args.revision_of:
            prompt += ('\n请通过research_begin.revision_of引用' + args.revision_of +
                       '，创建关联修订。核验继承来源与固定方法，原目录全部保留；在新目录重新写作、单独核对并发布。')
        if args.feedback_file:
            prompt += '\n本次需核实的公开资料反馈（意见不是事实认证，请对照原件）：\n' + args.feedback_file.read_text(encoding='utf-8')
        result['prompt'] = prompt
        write(output, result)  # stable ID before external work; never retry this POST
        try:
            with client.stream('POST', '/api/chat/stream', json={'conversation_id': result['conversation_id'],
                    'workspace_id': result['workspace_id'], 'model': result['model'],
                    'request_id': result['request_id'], 'message': prompt}) as response:
                response.raise_for_status()
                for line in response.iter_lines():
                    if not line.strip():
                        continue
                    event = json.loads(line)
                    if event.get('run_id'):
                        result['run_id'] = event['run_id']
                    result['last_event'] = event.get('event')
                    if event.get('event') in ('tool.started', 'tool.completed', 'run.accepted', 'run.completed', 'error'):
                        print(json.dumps({k: event.get(k) for k in ('event', 'run_id', 'seq')}, ensure_ascii=False), flush=True)
                    write(output, result)
            result['status'] = 'STREAM_ENDED'
        except Exception as exc:
            result.update(status='OBSERVATION_INTERRUPTED', error_type=type(exc).__name__)
            write(output, result)
            raise  # Observer failure does not authorize resubmission or cancel the backend.
        collect(client, result, output)


if __name__ == '__main__':
    main()
