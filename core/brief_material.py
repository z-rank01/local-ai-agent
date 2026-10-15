"""Prepare writing material from an authorised conversation's public receipts.

No retrieval, business inference, model call, database write or automatic
publication. The caller supplies cloud-safe protocol from ONE conversation
and the current workspace permission. Stock-side facts remain authoritative.
"""
from copy import deepcopy
from hashlib import sha256
import json
import re

from .stock_receipts import paired_receipts
from .stock_source_coverage import canonical_span, coverage_index
from .stock_financial_receipts import financial_fields_from_read
from .stock_company_context import context_index
from .stock_calculation_review import calculation_index


def _pick(row, keys):
    return {key: deepcopy(row[key]) for key in keys.split() if key in row}


def _encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False)


def prepare_material(messages, references, *, cloud_allowed):
    """Keep all received originals; never infer a task from prose or a title.

    This is a local preparation result, not a semantic approval. Exact duplicate
    material is collapsed; conflicting variants remain visible and are flagged.
    The returned diagnostics are local and are not part of the writing packet.
    """
    if cloud_allowed is not True:
        raise PermissionError('工作区尚未授权公开材料云端读取')
    if (not isinstance(references, (list, tuple)) or not references
            or any(not isinstance(r, str) or not re.fullmatch('[0-9a-f]{64}', r) for r in references)
            or len(set(references)) != len(references)):
        raise ValueError('须显式指定不重复的任务引用')
    receipts = list(paired_receipts(messages, allow_legacy_reference=False))
    known = {r.reference for r in receipts}
    if not set(references) <= known:
        raise ValueError('指定任务没有本会话成功公开回执，不替换引用')
    receipts = [r for r in receipts if r.reference in references]
    comparisons = context_index((), receipts=receipts)
    calculations = calculation_index((), receipts=receipts)
    tasks = []
    diagnostics = []
    for ref in references:
        own = [r for r in receipts if r.reference == ref]
        reads = [r.observation for r in own if r.tool_name == 'stock_public_evidence_read']
        sources, fields, coordinates, legacy_fields = {}, {}, {}, {}
        omitted = 0
        for obs in reads:
            event = obs.get('event_id')
            if not isinstance(event, str) or not re.fullmatch('[0-9a-f]{16}', event):
                omitted += 1
                continue
            for span in obs.get('excerpts', []):
                value = canonical_span(span, obs.get('url'))
                if value is None:
                    if isinstance(span, dict) and span.get('kind') in ('SOURCE_FIELD', 'PEER_SOURCE_FIELD'):
                        raw = canonical_span({**span, 'kind': 'ORIGINAL_TEXT'}, obs.get('url'))
                        if raw:
                            raw = {**raw, 'kind': span['kind'], 'event_id': event}
                            legacy_fields[_encoded(raw)] = raw
                    continue
                value = {'event_id': event, **value}
                key = _encoded(value)
                sources[key] = value
                coordinate = (event, value['url'], value['excerpt_id'])
                coordinates.setdefault(coordinate, set()).add(key)
            for field in financial_fields_from_read(obs):
                value = {'event_id': event, **field}
                fields[_encoded(value)] = value
        reports = [r.observation for r in own if r.tool_name == 'stock_report_read']
        pages = []
        if reports:
            latest = reports[-1]
            candidate = latest.get('answer_pages')
            if (isinstance(candidate, list) and candidate
                    and all(isinstance(p, dict) and p.get('kind') == 'local_report_page'
                            and isinstance(p.get('body'), str)
                            and isinstance(p.get('reader_revision'), str)
                            and re.fullmatch('[0-9a-f]{64}', p['reader_revision'])
                            and type(p.get('page_count')) is int
                            and type(p.get('requested_page')) is int
                            and 1 <= p['requested_page'] <= p['page_count'] for p in candidate)
                    and len({p['reader_revision'] for p in candidate}) == 1):
                pages = [_pick(p, 'body reader_revision requested_page page_count') for p in candidate]
        comparison = comparisons.get(ref) if pages else None
        # Directory entries locate unread fields; they are never writing facts.
        comparison_public = None
        if comparison:
            comparison_public = _pick(comparison, 'reader_revision comparable_period common_period')
            comparison_public['subjects'] = [
                {**_pick(s, 'name symbol role report_period'),
                 'metrics': [_pick(m, 'metric label status unit value_yuan raw_value report_period field location url kind event_id read_cursor')
                             for m in s['metrics']]}
                for s in comparison['subjects']]
        program = []
        for calc in (calculations.get(ref, []) if pages else []):
            value = _pick(calc, 'kind version symbol reader_revision amount_yuan annual_revenue_yuan annual_period ratio percent')
            if calc.get('annual_source'):
                value['annual_source'] = _pick(calc['annual_source'], 'kind metric unit symbol report_period value_yuan location event_id read_cursor url')
            if calc.get('service_mean'):
                mean = calc['service_mean']
                value['service_mean'] = _pick(mean, 'service_years mean_amount_yuan mean_percent')
                value['service_mean']['duration_source'] = _pick(mean['duration_source'], 'kind symbol quote event_id read_cursor location url published')
            program.append(value)
        conflicts = sum(len(v) > 1 for v in coordinates.values())
        financial_coordinates = {}
        for field in fields.values():
            key = (field['symbol'], field['financial_metric'], field['report_period'], field['unit'])
            financial_coordinates.setdefault(key, set()).add(field['value_yuan'])
        field_conflicts = sum(len(v) > 1 for v in financial_coordinates.values())
        gaps = []
        if not sources: gaps.append('没有可配套的已读公开原文；不代表原任务没有资料')
        if not pages: gaps.append('没有合法新版报告正文；未使用旧过程页代替')
        if conflicts: gaps.append('同一原文位置存在多个保存版本，未选择或拼接，写作前需核对')
        if field_conflicts: gaps.append('同证券指标期间存在不同原值，写作前需核对')
        tasks.append({'reference': ref, 'original_sources': list(sources.values()),
                      'source_field_text_not_new_binding': list(legacy_fields.values()),
                      'financial_fields': list(fields.values()), 'program_calculations': program,
                      'comparison_basis': comparison_public,
                      'prior_analysis_not_source_fact': pages,
                      'saved_source_coverage': coverage_index(reads, {ref}), 'gaps': gaps})
        diagnostics.append({'reference': ref, 'receipts': len(own), 'source_variants': len(sources),
                            'financial_fields': len(fields), 'source_conflicts': conflicts,
                            'financial_conflicts': field_conflicts, 'unbound_read_pages': omitted})
    packet = {'scope': '仅本会话成功回执中的已授权公开材料，不代表研究完成或资料最新',
              'reading_rules': '原文、程序计算与既有模型分析分列。保留反证和不确定程度；目录不等于已读，缺口不等于事实不存在。',
              'tasks': tasks}
    text = _encoded(packet)
    return {'packet': packet, 'sha256': sha256(text.encode()).hexdigest(),
            'diagnostics': {'tasks': diagnostics, 'characters': len(text),
                            'semantic_certification': False}}
