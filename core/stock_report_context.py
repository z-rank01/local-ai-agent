"""A typed explanation view of already authorised report receipts.

The stored messages and the legacy tool API remain unchanged. Detailed model
stage output stays available through the same conversation evidence reader.
Original source receipts are never replaced by this view.
"""
from __future__ import annotations

import json
import re

from .stock_source_quotes import source_task_index


def _body_pages(value):
    pages = value.get('answer_pages')
    if not isinstance(pages, list) or not pages:
        return False
    return all(isinstance(p, dict) and p.get('kind') == 'local_report_page'
               and isinstance(p.get('body'), str)
               and type(p.get('page_count')) is int and p['page_count'] >= 1
               and type(p.get('requested_page')) is int
               and 1 <= p['requested_page'] <= p['page_count'] for p in pages)


def report_material_view(messages, archive):
    """Prefer the current reader body; index whole legacy and prior bodies.

    Select only a task bound to actual paired successful tool receipts. No
    title/keyword routing, guessing references, re-generating conclusions or
    adding cloud material. Without a valid current body, retain all old pages.
    """
    last_user = max((i for i, row in enumerate(messages) if row.get('role') == 'user'), default=-1)
    if archive is None or last_user < 0:
        return messages, []
    known = {row['source_reference'] for row in source_task_index(messages)}
    requested = set(re.findall(r'(?<![0-9a-f])[0-9a-f]{64}(?![0-9a-f])', messages[last_user].get('content') or ''))
    selected = (requested | {row['source_reference'] for row in source_task_index(messages[last_user:])}) & known
    if not selected:
        return messages, []
    calls, reports, latest = {}, [], {}
    for position, row in enumerate(messages):
        if row.get('role') == 'assistant':
            for call in row.get('tool_calls') or []:
                fn = call.get('function', {})
                if fn.get('name') != 'stock_report_read':
                    continue
                try:
                    args = fn.get('arguments', {})
                    args = json.loads(args) if isinstance(args, str) else args
                    if isinstance(args, dict) and args.get('reference') in selected:
                        calls[call.get('id')] = args['reference']
                except (TypeError, ValueError):
                    continue
        if row.get('role') != 'tool' or row.get('tool_name') != 'stock_report_read':
            continue
        reference = calls.get(row.get('tool_call_id'))
        if not reference:
            continue
        try:
            envelope = json.loads(row.get('content') or '{}')
        except (TypeError, ValueError):
            continue
        if (not isinstance(envelope, dict) or envelope.get('error')
                or envelope.get('status') in ('FAILED', 'RULE_BLOCKED', 'ERROR', 'failed')):
            continue
        value = envelope.get('model_observation', envelope)
        if (not isinstance(value, dict) or value.get('error') or value.get('status') in ('FAILED', 'RULE_BLOCKED', 'ERROR', 'failed')
                or value.get('kind') != 'local_report_page'
                or value.get('source_reference', reference) != reference):
            continue
        reports.append((position, reference, envelope, value))
        if _body_pages(value):
            latest[reference] = position
    replacements, index = {}, []
    for position, reference, envelope, value in reports:
        if reference not in latest:
            continue
        already_indexed = value.get('material_view_version') == 'reader-material-index-v1'
        if already_indexed and (position == latest[reference] or not _body_pages(value)):
            continue
        if not isinstance(value.get('body'), str) and not _body_pages(value):
            continue
        row = messages[position]
        eid = value.get('legacy_material_evidence_id') if already_indexed else archive({**row, 'record_kind': 'report_model_material'})
        if not isinstance(eid, str) or not re.fullmatch('[0-9a-f]{64}', eid):
            raise ValueError('报告材料归档未返回有效标识')
        projected = dict(value)
        projected.pop('body', None)
        if position != latest[reference]:
            projected.pop('answer_pages', None)
        projected.update(material_view_version='reader-material-index-v1',
                         legacy_material_evidence_id=eid,
                         material_reading=(
                             '旧整篇页面和模型阶段材料已完整归档，可用context_evidence_read回读；'
                             '不是公告或政策原文。answer_pages若提供，是本任务最近读取的新版正文，'
                             '仍是已保存模型分析，保持其中部分成果与未完成项含义。'
                             '逐字引文、印发日期和资金适用范围须以实际公开来源读取回执为依据；'
                             '网页发布日期不自动等于文件印发日期，规定应收回不证明已实际收回。'
                             '若比较历史版本或查原阶段推断，请回读相应证据；不向用户展示内部索引。'))
        if 'model_observation' in envelope:
            projected = {**envelope, 'model_observation': projected}
        replacements[position] = {**row, 'content': json.dumps(projected, ensure_ascii=False)}
        index.append({'evidence_id': eid, 'source_reference': reference,
                      'kind': 'report_model_material_not_original_source'})
    return [replacements.get(i, row) for i, row in enumerate(messages)], index
