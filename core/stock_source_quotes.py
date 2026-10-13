"""Check explicitly labelled literal quotes against authorised original text.

This is a narrow delivery check, not a classifier for the user's research
direction or a verifier of every natural-language assertion.
"""
from __future__ import annotations

import json
import re

_LABEL = re.compile(r'(?:原文|原句|原话)(?:表述|引用|引文|原句|摘录|写道|写明|称|条款|内容|关键句|句子|(?=[:：]|\*{0,2}\s*$))|(?:逐字|准确)(?:引文|引用)|条款(?:位置|原文)|关键条款')
_QUOTES = re.compile(r'“([^”\n]+)”|"([^"\n]+)"')
_PARAPHRASE = re.compile(r'转述|概括|释义|意译|不是原文|非逐字')
READ_TOOLS = frozenset({'stock_public_evidence_find', 'stock_public_evidence_read',
                       'stock_report_read', 'stock_research_find', 'stock_theme_research_find',
                       'stock_market_brief_find', 'context_evidence_read'})


def may_start_literal_quote(text):
    # This inspects generated prose, never the user's research direction.
    # Ordinary introductory/partial output continues to stream unchanged.
    return bool(_LABEL.search(text) or re.search(r'原文|原句|原话|逐字|条款|准确引', text))


def original_texts(messages):
    """Only trusted read protocol rows; report/model outputs are not originals."""
    texts = []
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
        for excerpt in value.get('excerpts', []):
            if (isinstance(excerpt, dict) and excerpt.get('kind', 'ORIGINAL_TEXT') in
                    ('ORIGINAL_TEXT', 'PEER_ORIGINAL_TEXT') and isinstance(excerpt.get('text'), str)):
                texts.append(excerpt['text'])
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
            texts.extend(original_texts([{'role':'tool','tool_name':'stock_public_evidence_read','content':body}]))
    return texts


def unverified_quotes(reply, originals):
    """Return affected paragraphs; exact characters/spaces are intentional."""
    issues = []
    previous_intro = False
    for index, paragraph in enumerate(re.split(r'\n\s*\n', reply)):
        labelled = previous_intro
        previous_intro = False
        for line_index, line in enumerate(paragraph.splitlines()):
            match = _LABEL.search(line)
            if match:
                labelled = not bool(_PARAPHRASE.search(line))
            elif line.lstrip().startswith(('#', '|')):
                labelled = False
            if not labelled or _PARAPHRASE.search(line):
                continue
            for quote in _QUOTES.finditer(line[match.end():] if match else line):
                text = quote[1] if quote[1] is not None else quote[2]
                if not any(text in original for original in originals):
                    issues.append({'paragraph': index, 'line': line_index, 'quote': text})
        last_line = paragraph.splitlines()[-1] if paragraph.splitlines() else ''
        previous_intro = bool(_LABEL.search(last_line) and not _PARAPHRASE.search(last_line)
                              and not _QUOTES.search(last_line))
    return issues


def quote_feedback(issues):
    quotes = '\n'.join('- ' + item['quote'] for item in issues)
    return ('原文引述核验：下列标为原文/条款的引文未在已授权、实际读取的来源原文中找到。'
            '阶段分析、搜索目录、财报字段或模型推断不能替代逐字原文。\n' + quotes +
            '\n保留其他有效判断，只修正相关段落。原问题需要原文核对时，通过本轮现有公开证据查找/读取工具'
            '取得对应原文及末尾条件；也可明确标为转述并说明原文尚未核对。'
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
    kept.append('部分引述未完成原文核对，暂不作为依据；其他内容保留，原问题的来源核验仍有缺口。')
    return '\n\n'.join(kept)
