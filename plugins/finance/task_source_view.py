"""Task-bound view of existing authorised historical source receipts.

Explicit task references select the view, not the user's intent. Other task
receipts remain losslessly readable through the existing evidence archive.
Stored messages, tool arguments and current-turn results are never rewritten.
"""
import json
import re

from plugins.finance.source_quotes import original_records, source_task_index

VERSION = 'current-task-source-index-v1'
TOOLS = {'stock_public_evidence_find', 'stock_public_evidence_read'}
KINDS = {'public_evidence_find', 'public_evidence_read'}


def lookup_index(index, question):
    """Locate explicit tasks for both answer and source-parameter views.

    This narrows an index, not Broker permission or model tool arguments.
    The full receipts and other-task evidence pointers remain recoverable.
    """
    from plugins.finance.task_binding import reference_binding, explicit_references
    binding = reference_binding(index, question)
    requested = explicit_references(question)
    if not requested:
        note = ('\n此会话只有一个成功配套报告的任务定位，作为本问题材料指针；'
                '不代表已经判断问题用途，不改变原问题、权限或工具参数。'
                '若当前问题涉及其他材料，需保留定位缺口，不用此报告替代。'
                if binding['selection'] == 'single_report_receipt' else '')
        return {'rows':index,'selection':binding['selection'],'note':note,'version':binding['version']}
    selected=[row for row in index if row.get('source_reference') in binding['references']]
    known={row.get('source_reference') for row in selected}
    note=('\n本步骤定位索引仅列当前问题明确指定的任务；其他任务完整证据仍可沿已有evidence_id回读。'
          '不能因同URL或旧摘录编号改用其他任务引用，索引选择不改变Broker授权或自动替换工具参数。')
    if requested-known:
        note+='当前指定引用有未在成功回执索引中定位的项；不能以其他已知引用代替，需如实说明定位缺口。'
    return {'rows':selected,'selection':binding['selection'],
            'note':note,'version':'current-task-lookup-index-v1'}


def current_task_source_view(messages, archive):
    last_user=max((i for i,m in enumerate(messages) if m.get('role')=='user'),default=-1)
    if archive is None or last_user<0:return messages,[]
    requested=set(re.findall(r'(?<![0-9a-f])[0-9a-f]{64}(?![0-9a-f])',
                             messages[last_user].get('content') or ''))
    known={s['source_reference'] for s in source_task_index(messages)}
    read_tasks={r.get('source_reference') for r in original_records(messages)}
    # No implicit latest-task choice and no unknown-reference filtering.
    if not requested or not requested<=known or not requested & read_tasks:return messages,[]
    from plugins.finance.receipts import paired_receipts
    replacements, index = {}, []
    for receipt in paired_receipts(messages[:last_user], TOOLS):
        if receipt.reference in requested:
            continue
        position, message = receipt.position, receipt.message
        pair = (receipt.tool_name, receipt.reference)
        envelope = json.loads(message['content'])
        eid=archive({**message,'record_kind':'other_task_source_receipt'})
        if not isinstance(eid,str) or not re.fullmatch('[0-9a-f]{64}',eid):
            raise ValueError('历史来源回执归档未返回有效标识')
        pointer={'kind':'historical_other_task_source_index','view_version':VERSION,
                 'source_reference':pair[1],'evidence_id':eid,
                 'reading':'这是其他任务的历史来源回执，不是本轮指定任务的来源范围或事实。'
                           '原查找／读取结果含全部原文、反证、范围和游标，已完整归档。'
                           '若需比较旧快照或核对相关反证，用context_evidence_read回读；'
                           '不能把同URL的旧段数或游标套到当前任务，索引不改变读取权限。'}
        projected={**envelope,'model_observation':pointer} if 'model_observation' in envelope else pointer
        replacements[position]={**message,'content':json.dumps(projected,ensure_ascii=False)}
        index.append({'evidence_id':eid,'source_reference':pair[1],'kind':'other_task_source_receipt'})
    return [replacements.get(i,m) for i,m in enumerate(messages)],index
