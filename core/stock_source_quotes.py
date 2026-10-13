"""Check explicitly labelled literal quotes against authorised original text.

This is a narrow delivery check, not a classifier for the user's research
direction or a verifier of every natural-language assertion.
"""
from __future__ import annotations

import json
import re

_LABEL = re.compile(r'原文|原句|原话|(?:逐字|准确)(?:引文|引用)|条款(?:位置|原文|定位|为)|关键条款')
_QUOTES = re.compile(r'“([^”\n]+)”|"([^"\n]+)"')
_PARAPHRASE = re.compile(r'转述|概括|释义|意译|不是原文|非逐字')
_SEARCH_LABEL = re.compile(r'检索词|搜索词|关键词|检索示例|未找到|未出现|等字样')
_ABSENCE = re.compile(r'(?:文件|通知|原文|公告|文中|条款)[^\n。；]{0,45}?(?:未(?:明确)?(?:规定|出现|提及|找到|见)|没有|不存在|无[“"\w\u4e00-\u9fff])')
READ_TOOLS = frozenset({'stock_public_evidence_find', 'stock_public_evidence_read',
                       'stock_report_read', 'stock_research_find', 'stock_theme_research_find',
                       'stock_market_brief_find', 'context_evidence_read'})


def may_start_literal_quote(text):
    # This inspects generated prose, never the user's research direction.
    # Ordinary introductory/partial output continues to stream unchanged.
    return bool(_LABEL.search(text) or _ABSENCE.search(text))


def original_records(messages):
    """Only trusted read protocol rows; report/model outputs are not originals."""
    records = []
    recovered = {}
    for message in messages:
        if message.get('role') != 'tool' or message.get('tool_name') not in ('stock_public_evidence_read', 'context_evidence_read'):
            continue
        try:
            value = json.loads(message.get('content') or '{}')
        except (TypeError, ValueError):
            continue
        if not isinstance(value, dict):
            continue
        if value.get('error') or value.get('status') in ('FAILED', 'RULE_BLOCKED', 'ERROR', 'failed'):
            continue
        if message.get('tool_name') == 'context_evidence_read':
            meta = value.get('metadata') or {}
            if meta.get('role') != 'tool' or meta.get('tool_name') != 'stock_public_evidence_read':
                continue
            if (type(value.get('cursor')) is not int or not isinstance(value.get('content'), str)
                    or not isinstance(value.get('evidence_id'), str) or type(value.get('total_chars')) is not int):
                continue
            recovered.setdefault(value.get('evidence_id'), []).append(value)
            continue
        value = value.get('model_observation', value)
        if not isinstance(value, dict) or value.get('error') or value.get('kind') != 'public_evidence_read':
            continue
        records.append(value)
    for pieces in recovered.values():
        pieces = sorted(pieces, key=lambda p: p['cursor'])
        body, offset = '', 0
        for piece in pieces:
            if piece['cursor'] < offset:
                continue
            if piece['cursor'] != offset:
                break
            body += piece['content']
            offset += len(piece['content'])
        if pieces and offset == pieces[-1].get('total_chars'):
            records.extend(original_records([{'role':'tool','tool_name':'stock_public_evidence_read','content':body}]))
    return records


def original_texts(messages):
    return [excerpt['text'] for record in original_records(messages)
            for excerpt in record.get('excerpts', [])
            if isinstance(excerpt, dict) and excerpt.get('kind', 'ORIGINAL_TEXT') in
            ('ORIGINAL_TEXT', 'PEER_ORIGINAL_TEXT') and isinstance(excerpt.get('text'), str)]


def complete_sources(records):
    """Coverage is receipt based; a last page or a search miss is insufficient."""
    groups = {}
    for record in records:
        total = record.get('total_excerpts')
        if type(total) is not int or total <= 0 or not record.get('job_id') or not record.get('event_id'):
            continue
        key = (record['job_id'], record['event_id'], total, record.get('url'))
        group = groups.setdefault(key, {'ids': set(), 'titles': set(), 'tail': False})
        group['titles'].add(record.get('title') or '')
        group['tail'] |= record.get('next_cursor') == ''
        for span in record.get('excerpts', []):
            if (isinstance(span, dict) and span.get('kind', 'ORIGINAL_TEXT') in
                    ('ORIGINAL_TEXT', 'PEER_ORIGINAL_TEXT') and span.get('excerpt_id') and isinstance(span.get('text'), str)):
                group['ids'].add(span['excerpt_id'])
    return [{'title': title, 'url': key[3] or ''} for key, group in groups.items()
            if group['tail'] and len(group['ids']) == key[2] for title in group['titles'] if title]


def unverified_quotes(reply, originals):
    """Return affected paragraphs; exact characters/spaces are intentional."""
    issues = []
    previous_intro = False
    section_depth = None
    for index, paragraph in enumerate(re.split(r'\n\s*\n', reply)):
        labelled = previous_intro
        previous_intro = False
        for line_index, line in enumerate(paragraph.splitlines()):
            heading = re.match(r'^\s*(#{1,6})\s+', line)
            match = _LABEL.search(line)
            if heading:
                depth = len(heading[1])
                if section_depth is not None and depth <= section_depth:
                    section_depth = None
                if match and not _PARAPHRASE.search(line):
                    section_depth = depth
                labelled = section_depth is not None
            if match:
                labelled = not bool(_PARAPHRASE.search(line))
            elif heading:
                labelled = section_depth is not None
            elif line.lstrip().startswith('|'):
                labelled = False
            if not (labelled or section_depth is not None) or _PARAPHRASE.search(line) or _SEARCH_LABEL.search(line):
                continue
            quotes = [quote[1] if quote[1] is not None else quote[2]
                      for quote in _QUOTES.finditer(line)]
            if line.lstrip().startswith('>') and not quotes:
                quotes = [re.sub(r'^\s*>\s?', '', line)]
            for text in quotes:
                if not any(text in original for original in originals):
                    issues.append({'paragraph': index, 'line': line_index, 'quote': text})
        last_line = paragraph.splitlines()[-1] if paragraph.splitlines() else ''
        previous_intro = bool(_LABEL.search(last_line) and not _PARAPHRASE.search(last_line)
                              and not _QUOTES.search(last_line))
    return issues


def unverified_absence_claims(reply, sources):
    """Require explicit attribution to a fully read source for absence claims.

    This checks the delivery's evidence coverage, not the truth of every claim.
    It never selects a research direction from the user's message.
    """
    issues = []
    for index, paragraph in enumerate(re.split(r'\n\s*\n', reply)):
        for line_index, line in enumerate(paragraph.splitlines()):
            if not _ABSENCE.search(line) or re.search(r'尚未核对|尚未读完|不能断言|不代表|不能说|并不意味着|待核对', line):
                continue
            if not any((source['title'] in line or (source['url'] and source['url'] in line)) for source in sources):
                issues.append({'paragraph': index, 'line': line_index, 'quote': line.strip(),
                               'reason': 'SOURCE_ABSENCE_WITHOUT_COMPLETE_ATTRIBUTED_READ'})
    return issues


def quote_feedback(issues):
    quotes = '\n'.join('- ' + item['quote'] for item in issues)
    return ('来源交付核验：下列引文未得到逐字原文支持，或“文件没有该条款”的断言缺少完整、明确归属的原文阅读。'
            '阶段分析、搜索目录、财报字段或模型推断不能替代逐字原文。\n' + quotes +
            '\n保留其他有效判断，只修正相关段落。原问题需要原文核对时，通过本轮现有公开证据查找/读取工具'
            '取得对应原文及末尾条件；也可明确标为转述并说明原文尚未核对。'
            '若确需判断文件没有某项规定，须完整读取对应来源，并在该判断旁注明来源标题或链接。'
            '没有找到或没有读完不能写成原文没有该条款。不新增研究、恢复任务、改模型或重发未知调用。')


def isolate_quote_paragraphs(reply, issues):
    blocked = {item['paragraph'] for item in issues}
    paragraphs = re.split(r'\n\s*\n', reply)
    kept = []
    for index, text in enumerate(paragraphs):
        if index not in blocked:
            kept.append(text)
            continue
        lines = text.splitlines()
        if lines and all(line.lstrip().startswith('|') for line in lines if line.strip()):
            bad_lines = {item['line'] for item in issues if item['paragraph'] == index}
            kept.append('\n'.join(line for number, line in enumerate(lines) if number not in bad_lines))
    kept.append('部分引述或条款缺失判断未完成原文核对，暂不作为依据；其他内容保留，原问题的来源核验仍有缺口。')
    return '\n\n'.join(kept)
