"""Compose lossless material projections independently of Agent orchestration."""
from dataclasses import dataclass
import json
import re

from core.stock_source_quotes import prior_explanation_view, PRIOR_EXPLANATION_VIEW_VERSION
from core.stock_report_context import report_material_view
from core.stock_task_source_view import current_task_source_view
from core.stock_task_binding import history_binding
from core.stock_receipts import paired_receipts


@dataclass
class MaterialView:
    messages: list
    binding: dict | None
    priors: list
    reports: list
    other_sources: list
    duplicate_count: int = 0
    prior_version: str = PRIOR_EXPLANATION_VIEW_VERSION


def project_materials(history, archive):
    """Archive before replacement; callers retain history on any failure.

    Stored user/source messages and tool arguments are not mutated. The caller
    registers all returned evidence IDs before exposing the projected view.
    """
    last_user = max((i for i, row in enumerate(history) if row.get('role') == 'user'), default=-1)
    binding = history_binding(history, last_user) if last_user >= 0 else None
    view, priors = prior_explanation_view(history, archive)
    view, reports = report_material_view(view, archive, compact_catalogues=True)
    view, other_sources = current_task_source_view(view, archive)
    view, duplicates = duplicate_source_view(view, archive)
    other_sources += duplicates
    return MaterialView(view, binding, priors, reports, other_sources, len(duplicates))


def duplicate_source_view(messages, archive):
    """Index only identical historical observations; keep the latest full copy.

    Reference participates in identity even for old receipts lacking that field.
    Source text, counters, cursors, optional metadata and snapshot differences all
    prevent a match. Current-turn receipts are never replaced.
    """
    last_user = max((i for i, row in enumerate(messages) if row.get('role') == 'user'), default=-1)
    if archive is None or last_user < 0:
        return messages, []
    groups = {}
    for receipt in paired_receipts(messages[:last_user], {'stock_public_evidence_read'}):
        identity = json.dumps([receipt.reference, receipt.observation], sort_keys=True, ensure_ascii=False)
        groups.setdefault(identity, []).append(receipt)
    replacements, index = {}, []
    for group in groups.values():
        for receipt in group[:-1]:
            eid = archive({**receipt.message, 'record_kind': 'duplicate_source_receipt'})
            if not isinstance(eid, str) or not re.fullmatch('[0-9a-f]{64}', eid):
                raise ValueError('重复来源归档未返回有效标识')
            pointer = {'kind': 'historical_duplicate_source_index', 'evidence_id': eid,
                       'source_reference': receipt.reference,
                       'reading': '此历史来源回执与后续保留的完整回执逐项相同；原文、反证和参数已完整归档，可按证据编号回读。'}
            envelope = json.loads(receipt.message['content'])
            value = {**envelope, 'model_observation': pointer} if 'model_observation' in envelope else pointer
            replacements[receipt.position] = {**receipt.message, 'content': json.dumps(value, ensure_ascii=False)}
            index.append({'evidence_id': eid, 'source_reference': receipt.reference, 'kind': 'duplicate_source_receipt'})
    return [replacements.get(i, row) for i, row in enumerate(messages)], index
