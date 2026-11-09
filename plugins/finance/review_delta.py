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
    candidate = payload['candidate']
    source_ids = {row['id'] for row in candidate.get('sources', [])}
    evidence = payload['evidence']['sources']
    decisions = [{key:row.get(key) for key in ('stage_id','claim_id')}
                 for row in payload.get('decision_claims') or []]
    return fingerprint({k:payload.get(k) for k in ('question','methods','revision_context')}
                       | {'decision_claims':decisions,
                          'sources':{key:evidence[key] for key in sorted(source_ids) if key in evidence},
                          'role_artifacts':{k:v for k,v in payload['artifacts'].items() if k.startswith('role:')}})


def decision_claims(payload):
    stages = (payload.get('recipe') or {}).get('stages', [])
    claims = {(stage['id'],claim.get('id')):claim
              for stage in stages for claim in stage.get('claims', [])}
    decisions = []
    for row in payload.get('decision_claims') or []:
        claim = row.get('claim') or claims.get((row.get('stage_id'), row.get('claim_id')))
        if not claim:
            return None
        decisions.append({**row,'claim':claim})
    return decisions


def cited_material(payload, candidate, source_ids):
    evidence = payload.get('evidence', {}).get('sources', {})
    citations = list({fingerprint(row):row for row in candidate.get('sources', [])
                      if row['id'] in source_ids}.values())
    material = {}
    for key in sorted(source_ids):
        if key not in evidence:
            continue
        quotes = {row['quote'] for row in citations if row['id'] == key}
        source = evidence[key]
        material[key] = {**source,'received':[text for text in source.get('received', []) if text in quotes]}
    return citations, material


def related_decisions(decisions, delta):
    focus = repr(delta)
    fields = ('text','entity','metric','value','conditions','counterevidence','unresolved')
    return [row for row in decisions if any(isinstance(row['claim'].get(key),str) and
            row['claim'][key] in focus for key in fields)]


def related_findings(findings, delta):
    focus = repr(delta)
    sections = {h[key].strip(' #') for h in delta.get('markdown', [])
                for key in ('before_section','after_section')}
    return [row for row in findings if row['claim'] in focus
            or any(section and section in row['section'] for section in sections)]


def scoped_revision(payload, candidate, delta, scope):
    source_ids = {row['id'] for row in candidate.get('sources', [])}
    decisions = decision_claims(payload)
    if decisions is None:
        return None
    current = visible(candidate)
    local = set(delta) <= {'markdown','review'} and set(current['review']) <= {'limitations','conflicts'}
    previous = payload.get('previous_findings', [])
    open_findings = payload.get('open_findings', [])
    if local:
        decisions = related_decisions(decisions, delta)
        previous = related_findings(previous, delta)
        open_findings = related_findings(open_findings, delta)
        source_ids = revision_sources(payload, source_ids, delta, decisions, previous + open_findings)
    scope.update(mode='local' if local else 'expanded',
                 reason='核对正文差异、核心结论与未结问题' if local else
                        '标题、来源、计算或研究时点变化，核对变更及核心依据',
                 source_ids=sorted(source_ids))
    citations, original_material = cited_material(payload, candidate, source_ids)
    packet = {k:payload.get(k) for k in ('question','publication_context','decision_surface')}
    packet.update(diff=delta, basis=scope, decision_claims=decisions,
                  previous_findings=previous, open_findings=open_findings,
                  limitations=current['review'], sources=citations,
                  calculations=candidate.get('calculations', []),
                  cited_page_context=[row for row in payload.get('cited_page_context', [])
                                      if row['source_id'] in source_ids],
                  original_material=original_material)
    if not local:
        packet['outline'] = [line for line in candidate['markdown'].splitlines() if line.startswith('#')]
        packet['verified_artifacts'] = selected_artifacts(payload, source_ids, decisions,
                                                          candidate.get('sources', []))
    return packet, scope


def revision_sources(payload, source_ids, delta, decisions, findings):
    artifacts = selected_artifacts(payload, set(), decisions)
    refs = {ref for row in decisions for ref in row['claim'].get('evidence_refs', [])}
    refs.update(ref for artifact in artifacts.values()
                for ref in artifact.get('dependency_refs', {}).values())
    linked = {ref[7:] for ref in refs if isinstance(ref,str) and ref.startswith('source:')}
    linked.update(artifact['excerpt']['source_id'] for artifact in artifacts.values()
                  if artifact.get('kind') == 'source_excerpt')
    focus = repr([delta,findings])
    linked.update(key for key in source_ids if key in focus)
    return (linked & source_ids) or source_ids


def plan_revision(payload, candidate, baseline):
    current = visible(candidate)
    identity = material_identity(payload)
    scope = {'mode':'full','reason':'首稿或无可靠基准','material_sha256':identity,
             'parent':baseline.get('key') if baseline else None,
             'parent_evidence_sha256':baseline.get('evidence_sha256') if baseline else None}
    if not baseline:
        return full_revision(payload, candidate), scope
    delta = changes(visible(baseline['candidate']), current)
    scope['changed_fields'] = list(delta)
    if identity != baseline['material_sha256']:
        scope['reason'] = '所选角色成果、核心判断或引用来源变化'
        return full_revision(payload, candidate), scope
    if not delta:
        scope.update(mode='reuse', reason='仅内部编制记录变化')
        return {}, scope
    source_ids = {row['id'] for row in candidate.get('sources', [])}
    if not source_ids or not source_ids <= payload['evidence']['sources'].keys():
        scope['reason'] = '无法定位当前稿引用的实际材料'
        return payload, scope
    return scoped_revision(payload, candidate, delta, scope) or (payload, dict(
        scope, mode='full', reason='核心主张引用无法定位'))


def selected_artifacts(payload, source_ids, decisions, citations=None):
    artifacts = payload['artifacts']
    quotes = None if citations is None else {(row['id'],row['quote']) for row in citations}
    refs = {ref for row in decisions for ref in row['claim'].get('evidence_refs', [])}
    refs.update(k for k,v in artifacts.items()
                if v.get('excerpt',{}).get('source_id') in source_ids and
                (quotes is None or
                 (v['excerpt']['source_id'],v['excerpt'].get('quote')) in quotes))
    pending = list(refs)
    while pending:
        item = artifacts.get(pending.pop(), {})
        for ref in item.get('dependency_refs',{}).values():
            if isinstance(ref,str) and ref not in refs:
                pending.append(ref)
                refs.add(ref)
    return {k:artifacts[k] for k in sorted(refs) if k in artifacts}


def full_revision(payload, candidate):
    decisions = decision_claims(payload)
    if decisions is None:
        return payload
    source_ids = {row['id'] for row in candidate.get('sources', [])}
    citations, sources = cited_material(payload, candidate, source_ids)
    artifacts = selected_artifacts(payload, source_ids, decisions, citations)
    artifacts.update((ref,artifact) for ref,artifact in payload['artifacts'].items()
                     if artifact.get('kind') == 'role_claim_checks')
    selected = {(row.get('stage_id'),row.get('claim_id')) for row in decisions}
    claim_materials = [row for row in payload.get('claim_materials', [])
                       if (row.get('stage_id'),row.get('claim_id') or
                           (row.get('claim') or {}).get('id')) in selected]
    return {**payload, 'claim_materials':claim_materials,
            'evidence':{**payload['evidence'],'sources':sources}, 'artifacts':artifacts}


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
        tracked[key] = {'id':key,'section':issue['section'],'claim':issue['claim'],'state':'unverified',
                        'category':issue.get('category','core_answer'),
                        'severity':issue.get('severity','advisory')}
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
        focus = json.dumps([scoped['diff'],scoped.get('previous_findings',[]),
                            scoped.get('open_findings',[])],ensure_ascii=False)
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
