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
_ABSENCE = re.compile(r'(?:文件|通知|原文|公告|文中|条款)[^\n。；，|]*?(?:未(?:明确)?(?:规定|披露|公布|提供|出现|提及|找到|见)|没有|不存在|无(?!法|从)[“"\w\u4e00-\u9fff])')
_BRIEF_ABSENCE = re.compile(r'^(?:无(?!法|从|论)|没有|缺乏|不存在)[^\n。；：→|]*?(?:计划|时间表|条款|安排|数据|材料|信息|证据|预算|方案|说明|记录|细节|指标|成本|毛利率)')
_ABSENCE_QUALIFIER = re.compile(
    r'尚未核对|尚未读完|待核(?:对|查)?|待确认|不能(?:断言|确认|认定|推断|推出|证明|据|从|由)|'
    r'无法(?:判断|确认|确定|证明)|无从(?:判断|确认|确定|证明)|不代表|不能说|并不意味着|'
    r'如果|假如|假设|倘若|(?:^|[：:])若|未找到|未检索到|未命中|没有找到|尚未取得|尚未获取|'
    r'(?:无|没有|缺乏)(?:足够|充分|可靠|确凿|已核验|适用|完整)(?:的)?(?:证据|数据|信息)|'
    r'没有证据(?:证明|表明|支持|显示|确认)|'
    r'本页|已读片段|已提供材料|当前材料|当前已读范围|目前取得的材料|本次检索')
_READ_SCOPE = re.compile(r'^\s*(?:在|就)?(?:本页|已读片段|已提供材料|当前材料|当前已读范围|目前取得的材料|本次检索)(?:中|内|而言)?\s*$')


def _brief_absence(text, partial=False):
    # Short claims often live in a heading, list label or table cell rather
    # than a sentence containing "公告". This inspects output, never intent.
    text = re.sub(r'^\s*(?:#{1,6}\s+|\d+[.)、]\s*|[-+*]\s+|>\s*)', '', text)
    text = text.lstrip().lstrip('*_`')
    return bool((re.match(r'^(?:无(?!法|从|论)|没有|缺乏|不存在)', text) if partial else
                 _BRIEF_ABSENCE.search(text)))
READ_TOOLS = frozenset({'stock_public_evidence_find', 'stock_public_evidence_read',
                       'stock_report_read', 'stock_research_find', 'stock_theme_research_find',
                       'stock_market_brief_find', 'context_evidence_read'})


def may_start_literal_quote(text):
    # This inspects generated prose, never the user's research direction.
    # Ordinary introductory/partial output continues to stream unchanged.
    return bool(_LABEL.search(text) or _ABSENCE.search(text) or
                any(_brief_absence(cell, partial=True) for line in text.splitlines()
                    for cell in re.split(r'(?<!\\)\|', line)))


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


def source_task_index(messages):
    """Index authorised tool receipts, never parse user prose or stage hashes.

    Older receipts lack source_reference; link their successful response to its
    own tool call. This changes only the current model view, not stored history.
    """
    calls, rows = {}, {}
    for order, message in enumerate(messages):
        if message.get('role') == 'assistant':
            for call in message.get('tool_calls', []) or []:
                fn = call.get('function', {})
                if fn.get('name') not in ('stock_report_read', 'stock_public_evidence_find', 'stock_public_evidence_read'):
                    continue
                try:
                    args = fn.get('arguments', {})
                    args = json.loads(args) if isinstance(args, str) else args
                    token = args.get('reference', '')
                    if isinstance(token, str) and re.fullmatch('[0-9a-f]{64}', token):
                        calls[call.get('id')] = token
                except (TypeError, ValueError, AttributeError):
                    continue
        if message.get('role') != 'tool' or message.get('tool_name') not in ('stock_report_read', 'stock_public_evidence_find', 'stock_public_evidence_read'):
            continue
        try:
            value = json.loads(message.get('content') or '{}')
            if not isinstance(value, dict) or value.get('error'):
                continue
            value = value.get('model_observation', value)
            if not isinstance(value, dict) or value.get('kind') not in ('market_brief_page', 'local_report_page', 'public_evidence_find', 'public_evidence_read'):
                continue
            token = calls.get(message.get('tool_call_id'))
            if not token:
                continue
            row = rows.pop(token, {'source_reference': token, 'sources': {}})
            if value['kind'] in ('market_brief_page', 'local_report_page'):
                row['report_read'] = True
                row['_report_order'] = order
            items = value.get('items', []) if value['kind'] == 'public_evidence_find' else [value] if value['kind'] == 'public_evidence_read' else []
            for item in items:
                event_id = item.get('event_id') if isinstance(item, dict) else None
                if isinstance(event_id, str) and re.fullmatch('[0-9a-f]{16}', event_id):
                    row['sources'][event_id] = {key: item[key] for key in ('event_id', 'title', 'read_cursor', 'next_cursor', 'url') if key in item}
            rows[token] = row
        except (TypeError, ValueError):
            continue
    ordered = sorted(rows.values(), key=lambda row: row.get('_report_order', -1), reverse=True)
    return [{**{key: value for key, value in row.items() if key != '_report_order'},
             'sources': list(row['sources'].values())} for row in ordered]


def source_index_guidance(index):
    return ('[内部工具定位索引，最近读取的报告在前；不得把字段、编号或引用哈希展示为报告正文。'
            'sources只表示本会话成功查找/读取时登记的来源，不是任务全部资料；空列表表示尚未通过工具定位，'
            '不证明报告没有原文或该文件不存在。不是新的事实或原文。'
            '依原问题选择对应任务，事件与游标须配套使用source_reference，不用阶段文字里的旧引用替换；'
            '曾经的模型回答不能证明条款。]\n' + json.dumps(index, ensure_ascii=False))


def prior_explanation_view(messages, archive):
    """Index prior prose for a receipt-bound task without removing evidence.

    Task references locate existing receipts; they grant no permissions and do
    not classify the user's intent. Tool envelopes, user instructions, source
    text, other tasks and this turn's messages stay in the view unchanged.
    """
    last_user = max((i for i, row in enumerate(messages) if row.get('role') == 'user'), default=-1)
    if last_user < 0 or archive is None:
        return messages, []
    known = {row['source_reference'] for row in source_task_index(messages)}
    raw_tasks = {row.get('source_reference') for row in original_records(messages)
                 if any(span.get('kind', 'ORIGINAL_TEXT') in ('ORIGINAL_TEXT', 'PEER_ORIGINAL_TEXT')
                        for span in row.get('excerpts', []) if isinstance(span, dict))}
    current = messages[last_user:]
    requested = set(re.findall(r'(?<![0-9a-f])[0-9a-f]{64}(?![0-9a-f])', current[0].get('content') or ''))
    selected = (requested | {row['source_reference'] for row in source_task_index(current)}) & known & raw_tasks
    if not selected:
        return messages, []
    remove, index = {}, []
    starts = [i for i, row in enumerate(messages[:last_user]) if row.get('role') == 'user'] + [last_user]
    for start, end in zip(starts, starts[1:]):
        turn = messages[start:end]
        question = turn[0].get('content') or ''
        bound = set(re.findall(r'(?<![0-9a-f])[0-9a-f]{64}(?![0-9a-f])', question)) & known
        bound |= {row['source_reference'] for row in source_task_index(turn)}
        if not (bound & selected):
            continue
        for offset, row in enumerate(turn, start):
            if row.get('role') != 'assistant' or not row.get('content'):
                continue
            # Persist the full already-authorised visible message before
            # replacing its prose. Never save or summarise hidden reasoning.
            eid = archive({**row, 'record_kind': 'prior_model_explanation'})
            if not isinstance(eid, str) or not re.fullmatch('[0-9a-f]{64}', eid):
                raise ValueError('旧解释证据归档未返回有效标识')
            remove[offset] = row
            index.append({'evidence_id': eid, 'question': question,
                          'kind': 'prior_model_explanation_not_source_fact'})
    if not index:
        return messages, []
    guidance = {'role': 'system', 'content':
        '[同一已保存任务的旧模型解释已完整归档，以下只作回读索引，不是来源事实或本次输出模板。'
        '当前问题、原始来源与工具回执仍完整提供；不继承旧解释的未经核验数字、日期、公司名单或格式。'
        '若本次问题要复核上次解释/决定，可用context_evidence_read按evidence_id回读全部正文；'
        '索引不改变工具权限，不新增任务，内部编号不展示在用户正文。]\n' + json.dumps(index, ensure_ascii=False)}
    view = []
    for i, row in enumerate(messages):
        if i == last_user:
            view.append(guidance)
        if i not in remove:
            view.append(row)
        elif row.get('tool_calls'):
            view.append({**row, 'content': ''})
    return view, index


def complete_sources(records):
    """Coverage is receipt based; a last page or a search miss is insufficient."""
    groups = {}
    for record in records:
        total = record.get('total_excerpts')
        reference = record.get('source_reference')
        task = ('reference', reference) if isinstance(reference, str) and re.fullmatch('[0-9a-f]{64}', reference) else (
            ('legacy_job', record['job_id']) if record.get('job_id') else None)
        if type(total) is not int or total <= 0 or not task or not record.get('event_id'):
            continue
        # Broker cloud receipts intentionally omit internal job IDs. Their
        # authorised reference still binds every original span to one task;
        # never merge spans from different references to claim full coverage.
        key = (task, record['event_id'], total, record.get('url'))
        group = groups.setdefault(key, {'ids': set(), 'titles': set(), 'tail': False})
        group['titles'].add(record.get('title') or '')
        group['tail'] |= record.get('next_cursor') == ''
        for span in record.get('excerpts', []):
            if (isinstance(span, dict) and span.get('kind', 'ORIGINAL_TEXT') in
                    ('ORIGINAL_TEXT', 'PEER_ORIGINAL_TEXT') and span.get('excerpt_id') and isinstance(span.get('text'), str)):
                group['ids'].add(span['excerpt_id'])
    return [{'title': title, 'url': key[3] or ''} for key, group in groups.items()
            if group['tail'] and len(group['ids']) == key[2] for title in group['titles'] if title]


def repair_quote_format(reply, originals):
    """Restore a unique literal span, never infer words, dates or numbers.

    Only whitespace and trailing editorial ellipses are repairable. Keep the
    exact source spelling and let the ordinary literal checker reject all
    remaining differences. Multiple distinct source spellings are ambiguous.
    """
    edits = []
    for issue in unverified_quotes(reply, originals):
        quote = issue['quote']
        prefix = re.sub(r'(?:…+|\.{3,})$', '', quote).rstrip()
        omitted = prefix != quote.rstrip()
        target = ''.join(prefix.split())
        if not target:
            continue
        matches = set()
        for source in originals:
            positions = [i for i, char in enumerate(source) if not char.isspace()]
            normalized = ''.join(source[i] for i in positions)
            start = normalized.find(target)
            while start >= 0:
                matches.add(source[positions[start]:positions[start + len(target) - 1] + 1])
                start = normalized.find(target, start + 1)
        if len(matches) != 1:
            continue
        exact = matches.pop()
        # Change only this checked quote on this line, not matching prose,
        # links or unlabelled discussion elsewhere in the answer.
        paragraphs = re.split(r'\n\s*\n', reply)
        lines = paragraphs[issue['paragraph']].splitlines()
        line = lines[issue['line']]
        changed = line.replace(quote, exact, 1)
        if omitted:
            changed += '（引文节选，末尾省略）'
        if changed == line:
            continue
        lines[issue['line']] = changed
        paragraphs[issue['paragraph']] = '\n'.join(lines)
        reply = '\n\n'.join(paragraphs)
        edits.append({**issue, 'replacement': exact,
                      'reason': 'TRAILING_ELLIPSIS' if omitted else 'WHITESPACE_ONLY'})
    return reply, edits


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
                text = re.sub(r'^\s*>\s?', '', line)
                # A Markdown label introduces the following quote; it is not
                # itself a claim that these words occur in the document.
                label = re.sub(r'[*_`]', '', text).strip()
                if re.fullmatch(r'(?:原文|原句|原话|准确引文|逐字引文)(?:引文|引用|条款)?[：:]?', label):
                    continue
                if re.fullmatch(r'\((?:excerpt_?id|正文字符|来源位置)[：:].*\)', label):
                    # A standalone location annotation is not a literal quote.
                    # This does not certify its source ID or numeric position.
                    continue
                quotes = [text]
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
    fence = None
    for index, paragraph in enumerate(re.split(r'\n\s*\n', reply)):
        for line_index, line in enumerate(paragraph.splitlines()):
            marker = re.match(r'^\s*(`{3,}|~{3,})', line)
            if fence is not None:
                if marker and marker[1][0] == fence[0] and len(marker[1]) >= len(fence):
                    fence = None
                continue
            if marker:
                fence = marker[1]
                continue
            table = line.lstrip().startswith('|')
            # A qualifier belongs to its clause/cell, not to every assertion
            # later on the same line. Preserve the original line coordinates
            # for the existing local isolation and saved failure protocol.
            sentences = [line] if table else re.split(r'[。；]', line)
            bad = False
            for sentence in sentences:
                scoped = False
                clauses = re.split(r'(?<!\\)\|', sentence) if table else sentence.split('，')
                for clause in clauses:
                    if not table and _READ_SCOPE.fullmatch(clause):
                        scoped = True
                    if scoped or not (_ABSENCE.search(clause) or _brief_absence(clause)) or _ABSENCE_QUALIFIER.search(clause):
                        continue
                    attribution = line if table else clause
                    if not any((source.get('title') and source['title'] in attribution) or
                               (source.get('url') and source['url'] in attribution) for source in sources):
                        bad = True
                        break
                if bad:
                    break
            if bad:
                issues.append({'paragraph': index, 'line': line_index, 'quote': line.strip(),
                               'reason': 'SOURCE_ABSENCE_WITHOUT_COMPLETE_ATTRIBUTED_READ'})
    return issues


def quote_feedback(issues):
    quotes = '\n'.join('- ' + item['quote'] for item in issues)
    return ('来源交付核验：下列引文未得到逐字原文支持，或“文件没有该条款”的断言缺少完整、明确归属的原文阅读。'
            '阶段分析、搜索目录、财报字段或模型推断不能替代逐字原文。\n' + quotes +
            '\n保留其他有效判断，只修正相关段落。原问题需要原文核对时，通过本轮现有公开证据查找/读取工具'
            '取得对应原文及末尾条件；也可明确标为转述并说明原文尚未核对。'
            '列表标题和表格中的“无计划”“无条款”等也属于缺失断言；只确认某步骤尚未发生，'
            '不能推成没有计划或没有安排。尚未核对时说明已读范围与待核内容，不把不确定改成已证实没有。'
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
    cleaned=prune_empty_sections('\n\n'.join(kept))
    return cleaned+'\n\n部分引述或条款缺失判断未完成原文核对，暂不作为依据；其他内容保留，原问题的来源核验仍有缺口。'


def prune_empty_sections(text):
    """Remove empty scaffolding after local isolation, preserving code fences."""
    lines=text.splitlines();fenced=set();fence=None
    for i,line in enumerate(lines):
        marker=re.match(r'^\s*(`{3,}|~{3,})',line)
        if fence is not None:
            fenced.add(i)
            if marker and marker[1][0]==fence[0] and len(marker[1])>=len(fence):fence=None
        elif marker:
            fence=marker[1];fenced.add(i)
    remove=set();i=0
    while i<len(lines):
        if i in fenced or not lines[i].strip().startswith('|'):
            i+=1;continue
        start=i
        while i<len(lines) and i not in fenced and lines[i].strip().startswith('|'):i+=1
        rows=lines[start:i]
        if len(rows)==2 and all(re.fullmatch(r':?-{3,}:?',c.strip()) for c in rows[1].strip().strip('|').split('|')):
            remove.update(range(start,i))
    headings=[(i,len(m[1])) for i,line in enumerate(lines) if i not in fenced
              and (m:=re.match(r'^\s*(#{1,6})\s+',line))]
    def substance(i):
        line=lines[i].strip()
        return i not in remove and bool(line) and (i in fenced or
            not re.match(r'^#{1,6}\s|^(?:-{3,}|\*{3,}|_{3,})$',line))
    for position,(i,depth) in enumerate(headings):
        end=next((j for j,d in headings[position+1:] if d<=depth),len(lines))
        if not any(substance(j) for j in range(i+1,end)):remove.add(i)
    return '\n'.join(line for i,line in enumerate(lines) if i not in remove).strip()
