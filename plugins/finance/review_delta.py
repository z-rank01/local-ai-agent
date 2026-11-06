"""Plan a revision review from frozen public material; no model or file I/O."""
import difflib
import re

from harness.runtime.evidence import fingerprint


def visible(candidate):
    result = {k:v for k,v in candidate.items() if k not in ('editorial_notes','review')}
    result['review'] = {k:v for k,v in candidate.get('review', {}).items() if k != 'checks'}
    return result


def changes(before, after):
    result = {}
    for field in sorted(before.keys() | after.keys()):
        left, right = before.get(field), after.get(field)
        if left == right:
            continue
        if field != 'markdown':
            result[field] = {'before':left, 'after':right}
            continue
        a, b = left.splitlines(), right.splitlines()
        def section(lines, pos):
            return next((line for line in reversed(lines[:pos+1]) if line.startswith('#')), '')
        result[field] = [{'before_line':i+1, 'after_line':j+1,
                          'before_section':section(a,i), 'after_section':section(b,j), 'before':a[i:end],
                          'after':b[j:stop], 'context':b[max(j-1,0):j] + b[stop:stop+1]}
                         for kind,i,end,j,stop in difflib.SequenceMatcher(None,a,b,autojunk=False).get_opcodes()
                         if kind != 'equal']
    return result


def material_identity(payload):
    # Leave out execution bookkeeping (tool events, review attempts and working files).
    return fingerprint({k:payload.get(k) for k in ('question','methods','decision_claims','revision_context','received_sha256')}
                       | {'sources':payload['evidence']['sources'],
                          'role_artifacts':{k:v for k,v in payload['artifacts'].items() if k.startswith('role:')}})


def plan_revision(payload, candidate, baseline):
    current = visible(candidate)
    identity = material_identity(payload)
    scope = {'mode':'full','reason':'首稿或无可靠基准','material_sha256':identity,
             'parent':baseline.get('key') if baseline else None,
             'parent_evidence_sha256':baseline.get('evidence_sha256') if baseline else None}
    if not baseline:
        return payload, scope
    delta = changes(visible(baseline['candidate']), current)
    scope['changed_fields'] = list(delta)
    if identity != baseline['material_sha256']:
        scope['reason'] = '核对材料版本变化，重新覆盖引用与依赖'
        return payload, scope
    if not delta:
        scope.update(mode='reuse', reason='仅内部编制记录变化')
        return {}, scope
    if 'markdown' in delta and not any(line.strip() for line in
        set(baseline['candidate']['markdown'].splitlines()) & set(candidate['markdown'].splitlines())):
        scope['reason'] = '正文没有可对应的段落'
        return payload, scope
    local = set(delta) == {'review'} and set(current['review']) <= {'limitations','conflicts'}
    # Typographic edits preserving every non-space character have local scope.
    if set(delta) == {'markdown'}:
        local = all(''.join(''.join(h['before']).split()) == ''.join(''.join(h['after']).split())
                    for h in delta['markdown'])
        if not local:
            local = all(local_citation_edit(h) for h in delta['markdown'])
    source_ids = {s['id'] for s in candidate.get('sources', [])}
    if not source_ids or not source_ids <= payload['evidence']['sources'].keys() or set(delta) - {
            'review','markdown','title','stance','calculations','charts'}:
        scope['reason'] = '来源、期间或无法定位的字段变化'
        return payload, scope
    # Narrative without explicit claim links can affect distant paragraphs. Include
    # the complete decision surface and all selected claims/calculation dependencies.
    scope.update(mode='local' if local else 'expanded',
                 reason='局部文字或限制说明变化' if local else '正文或核心判断变化，覆盖全文与选用依据',
                 source_ids=sorted(source_ids))
    scoped = {k:payload.get(k) for k in ('question','publication_context','decision_surface',
                                         'previous_findings','decision_claims','cited_page_context')}
    stages = (payload.get('recipe') or {}).get('stages', [])
    claims = {(s['id'],c.get('id')):c for s in stages for c in s.get('claims', [])}
    decisions = []
    for row in payload.get('decision_claims') or []:
        claim = row.get('claim') or claims.get((row.get('stage_id'), row.get('claim_id')))
        if not claim:
            scope.update(mode='full',reason='核心主张引用无法定位')
            return payload, scope
        decisions.append({**row,'claim':claim})
    scoped['decision_claims'] = decisions
    scoped.update(diff=delta, basis=scope, limitations=current['review'],
                  sources=candidate['sources'], calculations=candidate.get('calculations', []),
                  original_material={k:payload['evidence']['sources'][k] for k in sorted(source_ids)
                                     if k in payload['evidence']['sources']})
    if local:
        # The ranking and scope can be expressed anywhere, including the conclusion.
        scoped['conclusion_context'] = candidate['markdown']
    else:
        scoped['candidate'] = current
        scoped['claim_materials'] = payload.get('claim_materials')
        scoped['verified_artifacts'] = selected_artifacts(payload, source_ids, decisions)
    return scoped, scope


def local_citation_edit(hunk):
    old, new = '\n'.join(hunk['before']), '\n'.join(hunk['after'])
    if not old or not new or not hunk['before_section'] or hunk['before_section'] != hunk['after_section']:
        return False
    if any(term in old+new+hunk['after_section'] for term in
           ('排序','优先','结论','反证','限制','期间','同比','估值','高于','低于','排除')):
        return False
    refs = r'\bS[0-9a-f]{16}\b'
    return (bool(re.search(refs, old)) and re.findall(refs,old)==re.findall(refs,new)
            and re.findall(r'[+-]?\d+(?:\.\d+)?%?',old)==re.findall(r'[+-]?\d+(?:\.\d+)?%?',new))


def selected_artifacts(payload, source_ids, decisions):
    artifacts = payload['artifacts']
    refs = {ref for row in decisions for ref in row['claim'].get('evidence_refs', [])}
    refs.update(k for k,v in artifacts.items() if v.get('excerpt',{}).get('source_id') in source_ids)
    pending = list(refs)
    while pending:
        item = artifacts.get(pending.pop(), {})
        for ref in item.get('dependency_refs',{}).values():
            if isinstance(ref,str) and ref not in refs:
                pending.append(ref)
                refs.add(ref)
    return {k:artifacts[k] for k in sorted(refs) if k in artifacts}


def track_findings(previous, issues, responses):
    """Only the reviewer may mark an old finding resolved; silence leaves it open."""
    states = {r.get('id'):r for r in responses if isinstance(r,dict) and
              r.get('state') in ('addressed','unverified','retained','removed') and isinstance(r.get('reason'),str)}
    tracked = {r['id']:dict(r) for r in previous}
    for key,row in tracked.items():
        if key in states:
            row.update(state=states[key]['state'], reason=states[key]['reason'])
    for issue in issues:
        key = fingerprint([issue['section'],issue['claim']])
        tracked[key] = {'id':key,'section':issue['section'],'claim':issue['claim'],'state':'unverified'}
    return list(tracked.values())


def mark_numeric_advice(issues, table_rows):
    for issue in issues:
        if not re.search(r'\d', issue['action'] + issue['reason']):
            continue
        # Record only literal observations. A matched quote still needs its
        # period and column interpreted by the author.
        anchor = issue.get('source_anchor')
        if not isinstance(anchor,dict):
            anchor = {}
        quote = anchor.get('quote')
        located = isinstance(quote,str) and bool(quote.strip()) and any(
            row['source_id']==anchor.get('source_id') and quote in row['text'] for row in table_rows)
        issue['numeric_basis'] = ('引文可回查，须核实期间及行列' if located else '待核实，先查原表或计算输入')


def table_context(spans, citations, scoped=None):
    """Quote neighborhoods only from text actually received, never unseen pages."""
    rows = []
    focus = None
    if scoped and 'diff' in scoped:
        import json
        focus = json.dumps([scoped['diff'],scoped.get('previous_findings',[])],ensure_ascii=False)
    for item in citations:
        quote = item.get('quote','')
        if not quote or not re.search(r'\d', quote):
            continue
        if focus is not None and item['id'] not in focus and not any(
                n in focus for n in re.findall(r'\d[\d,]*\.\d+|\d{5,}', quote)):
            continue
        for span in spans.get(item['id'], []):
            pos = span.find(quote)
            if pos >= 0 and span.count(quote) == 1:
                rows.append({'source_id':item['id'], 'quote':quote,
                             'text':span[max(0,pos-1000):pos+len(quote)+350]})
                break
    return rows
