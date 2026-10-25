"""A typed explanation view of already authorised report receipts.

The stored messages and the legacy tool API remain unchanged. Detailed model
stage output stays available through the same conversation evidence reader.
Original source receipts are never replaced by this view.
"""
from __future__ import annotations

import json
import re

MATERIAL_VIEW_VERSION = "reader-material-index-v3"

def _body_pages(value):
    pages = value.get('answer_pages')
    if not isinstance(pages, list) or not pages:
        return False
    return all(isinstance(p, dict) and p.get('kind') == 'local_report_page'
               and isinstance(p.get('body'), str)
               and type(p.get('page_count')) is int and p['page_count'] >= 1
               and type(p.get('requested_page')) is int
               and 1 <= p['requested_page'] <= p['page_count'] for p in pages)


def report_material_view(messages, archive, *, compact_catalogues=False):
    """Prefer the current reader body; index whole legacy and prior bodies.

    Select only a task bound to actual paired successful tool receipts. No
    title/keyword routing, guessing references, re-generating conclusions or
    adding cloud material. Without a valid current body, retain all old pages.
    """
    last_user = max((i for i, row in enumerate(messages) if row.get('role') == 'user'), default=-1)
    if archive is None or last_user < 0:
        return messages, []
    from plugins.finance.task_binding import history_binding
    binding = history_binding(messages, last_user)
    selected = binding['references'] if binding['selection'] != 'all_known' else set()
    if not selected:
        return messages, []
    from plugins.finance.receipts import paired_receipts
    reports, latest = [], {}
    for receipt in paired_receipts(messages, {'stock_report_read'}):
        position, reference, value = receipt.position, receipt.reference, receipt.observation
        if reference not in selected or value['kind'] != 'local_report_page':
            continue
        envelope = json.loads(receipt.message['content'])
        reports.append((position, reference, envelope, value))
        if _body_pages(value):
            latest[reference] = position
    replacements, index = {}, []
    for position, reference, envelope, value in reports:
        if reference not in latest:
            continue
        already_indexed = value.get('material_view_version') in ('reader-material-index-v1','reader-material-index-v2','reader-material-index-v3')
        prior_comparison = position != latest[reference] and isinstance(value.get('comparison_context'),dict)
        has_directory = any(s.get('field_catalogue') is not None for s in
                            (value.get('comparison_context') or {}).get('subjects', []))
        needs_directory = compact_catalogues and position == latest[reference] and has_directory
        if already_indexed and not needs_directory and (position == latest[reference] or not _body_pages(value) and not prior_comparison):
            continue
        if not isinstance(value.get('body'), str) and not _body_pages(value) and not prior_comparison:
            continue
        row = messages[position]
        eid = value.get('legacy_material_evidence_id') if already_indexed else archive({**row, 'record_kind': 'report_model_material'})
        if not isinstance(eid, str) or not re.fullmatch('[0-9a-f]{64}', eid):
            raise ValueError('报告材料归档未返回有效标识')
        projected = dict(value)
        projected.pop('body', None)
        if position != latest[reference]:
            projected.pop('answer_pages', None)
            projected.pop('comparison_context', None)
        if needs_directory:
            from copy import deepcopy
            from plugins.finance.company_context import validated_catalogue
            context = deepcopy(projected['comparison_context'])
            for subject in context.get('subjects', []):
                try:
                    directory = validated_catalogue(subject)
                except (ValueError, TypeError, KeyError):
                    continue
                if directory is None:
                    continue
                subject.pop('field_catalogue', None)
                subject['field_catalogue_index'] = {
                    'version': 'archived-catalogue-index-v1', 'evidence_id': eid,
                    'field_count': len(directory['fields']), 'unbound_count': directory['unbound_count'],
                    'reading': '完整字段目录保存在本条成功报告的完整归档中，可用context_evidence_read回读；目录不是原值或已读证明。'}
            projected['comparison_context'] = context
        projected.update(material_view_version=MATERIAL_VIEW_VERSION if compact_catalogues else 'reader-material-index-v2',
                         legacy_material_evidence_id=eid,
                         material_reading=(
                             '旧整篇页面和模型阶段材料已完整归档，可用context_evidence_read回读；'
                             '不是公告或政策原文。answer_pages若提供，是本任务最近读取的新版正文，'
                             '仍是已保存模型分析，保持其中部分成果与未完成项含义。'
                             '逐字引文、印发日期和资金适用范围须以实际公开来源读取回执为依据；'
                             '网页发布日期不自动等于文件印发日期，规定应收回不证明已实际收回。'
                             '旧报告的完整比较字段目录也在同一归档中，当前最新目录保存在完整归档中；'
                             '若比较历史版本或查原阶段推断，请回读相应证据；不向用户展示内部索引。'))
        if 'model_observation' in envelope:
            projected = {**envelope, 'model_observation': projected}
        replacements[position] = {**row, 'content': json.dumps(projected, ensure_ascii=False)}
        index.append({'evidence_id': eid, 'source_reference': reference,
                      'kind': 'report_model_material_not_original_source'})
    return [replacements.get(i, row) for i, row in enumerate(messages)], index
