"""Exercise a linked financial revision through the original chat and artifact APIs."""
import argparse
from hashlib import sha256
import json
from pathlib import Path
import sys
import uuid

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from core.ui_session import session_token
from scripts.harness_holdout import scrub

BASE = 'http://127.0.0.1:9510'
WORKSPACE = 'b0546288a631'
MODEL = 'deepseek:deepseek-flash'
PREVIOUS = '3f9db00fc4b348f2b75e924a5f051d13'
PROMPT = '''只进行公开资料修订，不涉及账户、交易，不领取、恢复或重发任何股票后台任务。
请修订万德斯（688178）2026年半年度公开经营与现金流简报。原研究目录为 /workspace/reports/chat-research/3f9db00fc4b348f2b75e924a5f051d13 ，其中 research.json 是已保存研究记录。
请通过 research_begin 的 revision_of 引用这个 research.json，新建关联修订，不恢复旧运行，不修改旧目录中的任何文件。核验继承的原件、实际读取证据和固定方法版本，阅读原 model-original.md 与 reviews/1-result.json。新草稿、核对和发布必须在新研究目录完成；独立核对必须重新进行，旧 UNKNOWN 和旧结论不能当通过。
保留已有有效事实和反证，逐项解决原稿和审查中的这些问题：
1. 营业收入同比准确为78.74%，不得写“翻倍”。
2. 公司对增长的披露包括环保在手订单执行、收购安徽嘉奇和芜湖铭豪并表等因素；不得单独把并表认定为增长主因。子公司报告期净利润不能直接证明原环保业务亏损或增长归因。
3. 未读附注不能写成“正文没有解释”；缺少坏账准备余额、账龄等资料时不能判断“减值计提偏轻”。
4. 诉讼只能表述为公司预计不会对日常经营产生重大影响，但案件结果和损益影响仍不确定；不得当作已确定风险或已消除风险。
5. 销售商品、提供劳务收到的现金约375,871,341.81元，营业收入370,657,675.72元，比率约101%含增值税和前期应收回收，不能直接代表当期回款质量。
6. 预计负债整体不能直接与单一案件索赔额比较来暗示少提。
7. 已保存半年报第38页支持“以现金方式股权收购”和“自有资金”，可准确描述收购方式，但不得据此推导合并利润归因。
请保留来源、期间、单位和可复核计算；不要伪造原文或补造已读附注。如材料不足，明确保留缺口。
请完成整份修订稿，进入核对，按实际来源修订后发布HTML，并在最终答复中给出新报告卡链接。不要只保存草稿或给出修改建议。'''


def digest(path):
    return sha256(path.read_bytes()).hexdigest()


def finalize_incomplete(run_id):
    import asyncio
    import re
    from core.workspaces import WorkspaceManager, tool_token
    from core.conversation_store import ConversationStore
    from plugins.finance.research.session import ResearchSession
    from plugins.finance.research.revision import REFERENCE
    if not re.fullmatch('[a-f0-9]{12}', run_id):
        raise SystemExit('运行 ID 须为12位十六进制')
    output = ROOT / 'data/private/harness-finance-revision' / f'finalize-{run_id}.json'
    if output.exists():
        raise SystemExit('该收尾记录已存在，请保留原证据')
    async def operation():
        headers = {'X-Harness-Session': session_token(), 'Origin': BASE}
        async with httpx.AsyncClient(base_url=BASE, headers=headers,
                                     timeout=httpx.Timeout(90, connect=15), trust_env=False) as client:
            response = await client.get('/api/runs/' + run_id)
            response.raise_for_status()
            run = response.json()
            if run.get('status') not in ('FAILED', 'STOPPED', 'INTERRUPTED'):
                raise RuntimeError('只有已终止且未正常收尾的运行可以执行此状态修复')
            active = [row['id'] for row in (await client.get('/api/runs')).json()['runs']
                      if row.get('workspace_id') == run.get('workspace_id')
                      and row.get('status') in ('RUNNING', 'PAUSING', 'PAUSED', 'STOPPING')]
            if active:
                raise RuntimeError('所属工作区仍有活跃运行，不能补写研究状态：' + ','.join(active))
            workspace = WorkspaceManager(ConversationStore(), ROOT / 'data/workspace').get(run['workspace_id'])
            async with httpx.AsyncClient(base_url=f'http://127.0.0.1:{workspace["files_port"]}',
                        headers={'X-Workspace-Token': tool_token(workspace['id'])},
                        timeout=90, trust_env=False) as files:
                class OriginalTools:
                    async def dispatch(self, tool, params, session):
                        if tool not in ('file_read', 'file_write'):
                            raise ValueError('本次状态收尾只允许读取和保存既有研究记录')
                        response = await files.post('/tool/' + tool, json=params)
                        response.raise_for_status()
                        return response.json()
                research = ResearchSession(OriginalTools(), conversation_id=run['conversation_id'],
                                           model=run['model'], allowed=True)
                from core.run_store import RunStore
                checkpoint = RunStore(ConversationStore()).get(run_id)['checkpoint']
                research_id = checkpoint.get('turn', {}).get('state', {}).get('record', {}).get('id')
                if not re.fullmatch('[a-f0-9]{32}', research_id or ''):
                    raise RuntimeError('检查点没有可核验的研究身份，未修改任何文件')
                reference = f'/workspace/reports/chat-research/{research_id}/research.json'
                saved = await research.read_json(reference)
                if saved.get('status') != 'IN_PROGRESS' or saved.get('id') != REFERENCE.fullmatch(reference).group(1):
                    raise RuntimeError('研究已收尾或身份不一致，不能改写状态')
                if saved.get('conversation_id') != run['conversation_id'] or saved.get('model') != run['model']:
                    raise RuntimeError('研究会话或冻结模型与失败运行不一致')
                index = await research.read_json(saved['directory'] + '/evidence-index.json')
                before = sha256(json.dumps(saved, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
                research.record = saved
                research.sources = saved.get('sources', {})
                research.methods = set(saved.get('methods', []))
                research.trace = saved.get('trace', [])
                research.evidence_requests = saved.get('evidence_requests', {})
                research.review_records = saved.get('reviews', {})
                research.excerpts = saved.get('excerpts', {})
                research.read_text = {key: list(row.get('received', []))
                                      for key, row in index.get('sources', {}).items()}
                research.evidence_notes = {key: row.get('annotation', {})
                                           for key, row in index.get('sources', {}).items()}
                await research.incomplete()
                evidence = {'run_id': run_id, 'research_id': saved['id'], 'previous_status': 'IN_PROGRESS',
                            'status': 'DELIVERY_INCOMPLETE', 'before_sha256': before,
                            'research_sha256': sha256(json.dumps(research.record, ensure_ascii=False, sort_keys=True).encode()).hexdigest(),
                            'model_requests': 0, 'replayed': False}
                output.write_text(json.dumps(evidence, ensure_ascii=False, indent=2), encoding='utf-8')
                print(json.dumps(evidence, ensure_ascii=False))
    asyncio.run(operation())


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--finalize-run', default='')
    parser.add_argument('--variant', default='v1')
    parser.add_argument('--previous', default=PREVIOUS)
    parser.add_argument('--collect', action='store_true', help='只读取已完成的运行，不重新提交')
    parser.add_argument('--extra', default='')
    args = parser.parse_args()
    if args.finalize_run:
        finalize_incomplete(args.finalize_run)
        return
    import re
    if not re.fullmatch('[a-f0-9]{32}', args.previous):
        parser.error('原研究 ID 须为32位十六进制')
    host = ROOT / 'data/workspace/.harness' / WORKSPACE
    previous = host / 'reports/chat-research' / args.previous
    inputs = {str(path.relative_to(previous)): digest(path)
              for path in previous.rglob('*') if path.is_file()}
    output = ROOT / 'data/private/harness-finance-revision' / (args.variant + ('-collected.json' if args.collect else '.json'))
    if output.exists():
        raise SystemExit('此验收记录已存在，保留原记录；请使用新变体')
    output.parent.mkdir(parents=True, exist_ok=True)
    if args.collect:
        result = json.loads(output.with_name(args.variant + '.json').read_text(encoding='utf-8'))
        if not result.get('run_id'):
            raise SystemExit('尚无运行 ID，不能只读收集')
        result.update(status='COLLECTED', collection='read_only')
    else:
        result = {'variant': args.variant, 'workspace_id': WORKSPACE, 'model': MODEL,
                  'previous_id': args.previous, 'request_id': uuid.uuid4().hex,
                  'prompt': PROMPT.replace(PREVIOUS, args.previous) + args.extra,
                  'inputs': inputs, 'status': 'SUBMITTED'}
        output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    events = []
    headers = {'X-Harness-Session': session_token(), 'Origin': BASE}
    with httpx.Client(base_url=BASE, headers=headers, timeout=httpx.Timeout(3600, connect=20), trust_env=False) as client:
        if not args.collect:
            try:
                with client.stream('POST', '/api/chat/stream', json={
                        'workspace_id': WORKSPACE, 'model': MODEL,
                        'message': result['prompt'], 'request_id': result['request_id']}) as response:
                    if response.is_error:
                        result['status'] = 'HTTP_ERROR'
                        result['error'] = response.read().decode('utf-8', errors='replace')[:3000]
                    else:
                        for line in response.iter_lines():
                            if not line.strip():
                                continue
                            event = json.loads(line)
                            events.append(scrub(event))
                            if event.get('run_id') and not result.get('run_id'):
                                result['run_id'] = event['run_id']
                                output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
                        result['status'] = 'STREAM_COMPLETE'
            except (httpx.HTTPError, OSError, ValueError) as exc:
                result['status'] = 'UNKNOWN' if result.get('run_id') else 'NOT_STARTED'
                result['error'] = type(exc).__name__ + ': ' + str(exc)
        if result.get('run_id'):
            run = client.get('/api/runs/' + result['run_id'])
            if run.is_success:
                result['run'] = run.json()
            cid = result.get('run', {}).get('conversation_id')
            if cid:
                result['conversation_id'] = cid
                result['events'] = events
                messages = client.get(f'/api/conversations/{cid}/messages')
                messages.raise_for_status()
                rows = messages.json()
                result['tool_calls'] = [scrub(row) for row in rows if row.get('role') == 'tool']
                result['assistant'] = [row.get('content', '') for row in rows if row.get('role') == 'assistant']
                revisions = [row['tool_result'] if isinstance(row.get('tool_result'), dict) else json.loads(row['tool_result'])
                             for row in result['tool_calls'] if row.get('tool_name') == 'research_begin' and row.get('tool_result')]
                publications = [row['tool_result'] if isinstance(row.get('tool_result'), dict) else json.loads(row['tool_result'])
                                for row in result['tool_calls'] if row.get('tool_name') == 'brief_publish' and row.get('tool_result')]
                receipt = next((row for row in reversed(revisions) if row.get('revision_of')), {})
                published = next((row for row in reversed(publications) if row.get('status') == 'PUBLISHED'), {})
                result['revision_receipt'] = receipt
                result['publication'] = published
                research_id = receipt.get('research_id', '')
                research = host / 'reports/chat-research' / research_id / 'research.json'
                checks = {'originals_unchanged': all(digest(path) == inputs[str(path.relative_to(previous))]
                                                     for path in previous.rglob('*') if path.is_file())
                            and set(inputs) == {str(path.relative_to(previous)) for path in previous.rglob('*') if path.is_file()},
                          'linked_previous': receipt.get('revision_of', {}).get('research_id') == args.previous,
                          'new_research': bool(research_id and research_id != args.previous and research.is_file()),
                          'published': bool(published)}
                if research.is_file():
                    saved = json.loads(research.read_text(encoding='utf-8'))
                    prior = json.loads((previous / 'research.json').read_text(encoding='utf-8'))
                    checks['inherited_sources'] = set(prior.get('sources', {})) <= set(saved.get('sources', {}))
                    result['reviews'] = saved.get('reviews', {})
                    matching = [row for row in saved.get('reviews', {}).values()
                                if row.get('candidate_sha256') == published.get('sha256')]
                    checks['fresh_review_complete'] = bool(matching) and all(
                        row.get('status') == 'COMPLETE' for row in matching)
                    checks['no_inherited_unknown'] = not any(
                        row.get('status') == 'UNKNOWN' for row in saved['reviews'].values())
                    result['blocking_findings'] = [issue for row in saved['reviews'].values()
                        for issue in row.get('issues', []) if issue.get('severity') == 'blocking']
                report = host / published.get('path', '/nonexistent').removeprefix('/workspace/')
                checks['artifact_exists'] = report.is_file()
                if report.is_file():
                    result['artifact_sha256'] = digest(report)
                    result['artifact_path'] = str(report)
                result['checks'] = checks
                result['needs_content_review'] = True
        result['events'] = events
        output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({'output': str(output), 'run_id': result.get('run_id'),
                      'conversation_id': result.get('conversation_id'), 'status': result.get('status'),
                      'run_status': result.get('run', {}).get('status'), 'checks': result.get('checks')}, ensure_ascii=False))


if __name__ == '__main__':
    main()
