"""Task-bound view of existing authorised historical source receipts.

Explicit task references select the view, not the user's intent. Other task
receipts remain losslessly readable through the existing evidence archive.
Stored messages, tool arguments and current-turn results are never rewritten.
"""
import json
import re

from .stock_source_quotes import original_records, source_task_index

VERSION = 'current-task-source-index-v1'
TOOLS = {'stock_public_evidence_find', 'stock_public_evidence_read'}
KINDS = {'public_evidence_find', 'public_evidence_read'}


def current_task_source_view(messages, archive):
    last_user=max((i for i,m in enumerate(messages) if m.get('role')=='user'),default=-1)
    if archive is None or last_user<0:return messages,[]
    requested=set(re.findall(r'(?<![0-9a-f])[0-9a-f]{64}(?![0-9a-f])',
                             messages[last_user].get('content') or ''))
    known={s['source_reference'] for s in source_task_index(messages)}
    read_tasks={r.get('source_reference') for r in original_records(messages)}
    # No implicit latest-task choice and no unknown-reference filtering.
    if not requested or not requested<=known or not requested & read_tasks:return messages,[]
    calls={};replacements={};index=[]
    for position,message in enumerate(messages[:last_user]):
        if message.get('role')=='assistant':
            for call in message.get('tool_calls') or []:
                fn=call.get('function') or {}
                if fn.get('name') not in TOOLS:continue
                try:
                    args=fn.get('arguments',{})
                    args=json.loads(args) if isinstance(args,str) else args
                    ref=args.get('reference') if isinstance(args,dict) else None
                    if (isinstance(call.get('id'),str) and call['id'] and isinstance(ref,str)
                            and re.fullmatch('[0-9a-f]{64}',ref)):
                        calls[call.get('id')]=(fn['name'],ref)
                except (TypeError,ValueError):continue
        if message.get('role')!='tool' or message.get('tool_name') not in TOOLS:continue
        pair=calls.get(message.get('tool_call_id'))
        if not pair or pair[0]!=message['tool_name'] or pair[1] in requested:continue
        try:envelope=json.loads(message.get('content') or '{}')
        except (TypeError,ValueError):continue
        if not isinstance(envelope,dict) or envelope.get('error') or envelope.get('status') in ('FAILED','RULE_BLOCKED','ERROR','failed'):continue
        value=envelope.get('model_observation',envelope)
        expected='public_evidence_read' if pair[0]=='stock_public_evidence_read' else 'public_evidence_find'
        if (not isinstance(value,dict) or value.get('kind')!=expected or value.get('error')
                or value.get('status') in ('FAILED','RULE_BLOCKED','ERROR','failed')
                or value.get('source_reference',pair[1])!=pair[1]):continue
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
