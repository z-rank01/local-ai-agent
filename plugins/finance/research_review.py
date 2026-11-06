"""Candidate identity and a clean, same-model review context."""
import json
import re
from pathlib import Path
from harness.runtime.evidence import model_evidence, fingerprint


def previous_findings(records):
    complete=[r for r in records.values() if r.get('status')=='COMPLETE' and r.get('attempts')]
    if not complete:return []
    latest=max(complete,key=lambda r:int(r['attempts'][-1].rsplit('/',1)[1]))
    # Carry core questions, not the previous reviewer's fix or optional scope expansion.
    return [{k:r[k] for k in ('section','claim','evidence')} for r in latest.get('issues',[])
            if r.get('category','core_answer') == 'core_answer']


def reference_review_claims(payload):
    """Use stage-scoped references only for exact copies in the review payload."""
    recipe = payload.get('recipe') or {}
    claims = {}
    for stage in recipe.get('stages', []):
        for claim in stage.get('claims', []):
            if claim.get('id'):
                key = (stage['id'], claim['id'])
                claims[key] = None if key in claims else claim
    for field in ('claim_materials', 'decision_claims'):
        rows = []
        for row in payload.get(field, []):
            claim = row.get('claim')
            if (isinstance(claim, dict) and row.get('claim_id', claim.get('id')) == claim.get('id')
                    and claim == claims.get((row.get('stage_id'), claim.get('id')))):
                row = {k: v for k, v in row.items() if k != 'claim'}
                row['claim_id'] = claim['id']
            rows.append(row)
        payload[field] = rows


def review_messages(question, candidate, research, system, findings=()):
    from .research.recipe import claim_materials, recipe_view
    from .research.material_view import author_material_view
    material = author_material_view(research)
    artifacts = material['artifacts']
    instructions=(Path(__file__).parent/'prompts/review.txt').read_text(encoding='utf-8')
    # The writer's self-checks are not independent evidence. Preserve the original
    # submission elsewhere; only remove this affirmation from the review view.
    view={key:value for key,value in candidate.items() if key not in ('review','editorial_notes')}
    narrative=re.sub(r'(?m)^#{1,6}[ \t]+[^\n]*(?:\n|$)','',candidate.get('markdown') or '')
    opening=next((block for block in re.split(r'\n\s*\n',narrative.strip()) if block.strip()), '')
    payload={'question':question,
             'publication_context':{'phase':'prepublication', 'subject':'candidate_content',
                 'host_delivery_after_publish':['report_path','report_card','refresh_readback'],
                 'instruction':'只核对当前稿对原问题的回答；正式链接和报告卡由宿主在明确发布后生成，不能要求候选稿预置未来链接。'},
             'decision_surface':{**{k:candidate.get(k) for k in ('title','stance','as_of')},'opening':opening},
             'candidate':view,'limitations':candidate.get('review',{}).get('limitations',[]),
             'known_conflicts':candidate.get('review',{}).get('conflicts',[]),
             'recipe':recipe_view((getattr(research,'record',None) or {}).get('recipe'),
                 artifacts, candidate),
             'claim_materials':claim_materials((getattr(research,'record',None) or {}).get('recipe'),
                 artifacts, candidate),
             'revision_context':(getattr(research,'record',None) or {}).get('revision_context'),
             'evidence':material['evidence'],
             'artifacts':artifacts,
             'role_batches':(getattr(research,'record',None) or {}).get('role_batches',{}),
             'decision_claims':(getattr(research,'record',None) or {}).get('decision_claims',[]),
             'methods':research.method_text,'previous_findings':list(findings)}
    if (getattr(research, 'record', None) or {}).get('research_contract_version') == 3:
        reference_review_claims(payload)
    # The caller's system belongs to the author, including tool workflow and
    # publication instructions. Review is a separate, tool-free role: inherit
    # materials and the frozen client, not the author's execution directives.
    return [{'role':'system','content':instructions},
            {'role':'user','content':json.dumps(payload,ensure_ascii=False)}]


def blocking_findings(issues):
    """Findings the reviewer marked ``blocking``; everything else is advice."""
    return [issue for issue in issues or [] if issue.get('severity') == 'blocking']


def review_blocks(review):
    """Whether a recorded review may stop publication.

    Off by default: the reviewer advises, and gating every draft on it is what
    stopped candidates from converging. ``PUBLISH_REVIEW_ENFORCE=true`` restores
    blocking, and even then only ``severity: blocking`` findings count.
    """
    from core import config
    if not config.PUBLISH_REVIEW_ENFORCE:
        return False
    return bool(blocking_findings((review or {}).get('issues')))


def parse_review(text):
    value=json.loads(text)
    if not isinstance(value,dict) or not isinstance(value.get('issues'),list) or not value.get('checks'):
        raise ValueError('单独核对未返回issues数组与具体checks，不能视为已完成核对')
    if not isinstance(value['checks'],list) or any(not isinstance(s,str) or not s.strip() for s in value['checks']):
        raise ValueError('单独核对checks必须是具体结果文字')
    for issue in value['issues']:
        if not isinstance(issue,dict) or any(not isinstance(issue.get(k),str) or not issue[k].strip()
                for k in ('section','claim','reason','evidence','action')):
            raise ValueError('单独核对的问题缺少对应陈述、依据或修改要求')
        # Missing severity means advice, not a publication blocker.
        severity=str(issue.get('severity') or 'advisory').strip().lower()
        issue['severity']=severity if severity in ('blocking','advisory') else 'advisory'
        if 'category' in issue:
            category = issue['category']
            issue['category'] = category if category in ('core_answer','optional','postpublication') else 'optional'
    reads=value.get('read_requests',[])
    if not isinstance(reads,list):raise ValueError('read_requests须为来源补读列表')
    for row in reads:
        if isinstance(row,dict) and row.get('pages') is None:
            row.pop('pages',None)
        if (not isinstance(row,dict) or set(row)-{'source_id','terms','pages'} or
                not isinstance(row.get('source_id'),str) or not row['source_id'] or
                not isinstance(row.get('terms'),list) or not row['terms'] or
                any(not isinstance(t,str) or not t.strip() for t in row['terms']) or
                'pages' in row and (not isinstance(row['pages'],list) or not row['pages'] or
                                    any(type(p) is not int or p<1 for p in row['pages']))):
            raise ValueError('补读只接受实际source_id、非空terms及可选真实页码，不接受路径或URL')
    result={'issues':value['issues'],'checks':value['checks'],'read_requests':reads,'semantic_certification':False}
    if 'claim_checks' in value:
        claims=value['claim_checks']
        if not isinstance(claims,list):raise ValueError('claim_checks须为核心陈述对照列表')
        for claim in claims:
            if (not isinstance(claim,dict) or not isinstance(claim.get('claim'),str) or not claim['claim'].strip()
                    or any(not isinstance(claim.get(k,''),str) for k in ('observed_scope','unresolved'))
                    or not isinstance(claim.get('evidence_refs',[]),list)):
                raise ValueError('核心陈述对照缺少原句或材料范围')
            claim['assessment']=str(claim.get('assessment','unknown')).lower()
            if claim['assessment'] not in ('supported','conditional','unsupported','unknown'):
                claim['assessment']='unknown'
            for ref in claim.get('evidence_refs',[]):
                if (not isinstance(ref,dict) or any(not isinstance(ref.get(k),str)
                        for k in ('source_id','quote'))):
                    raise ValueError('核心陈述对照的来源须有source_id与原句')
        # Model annotations cannot write host-owned provenance or certification.
        result['claim_checks']=[{**{k:claim[k] for k in
            ('claim','observed_scope','unresolved','assessment') if k in claim},
            'evidence_refs':[{k:ref[k] for k in ('source_id','quote','locator','observed_at') if k in ref}
                             for ref in claim.get('evidence_refs',[])]} for claim in claims]
    return result


def claim_provenance(claims, candidate, research):
    """Check literal references only; a model verdict is never a certification."""
    from .publication import literal_text
    body=[literal_text(candidate[k]) for k in ('title','markdown')]
    result=[]
    for index,claim in enumerate(claims):
        refs=[]
        for ref in claim.get('evidence_refs',[]):
            known=ref['source_id'] in research.sources
            quote=literal_text(ref['quote'])
            refs.append({'source_id':ref['source_id'],'known_source':known,
                'quote_received':bool(known and quote and any(quote in literal_text(span)
                    for span in research.read_text.get(ref['source_id'],[]))),
                'observation_date_is_model_declared':True})
        result.append({'index':index,'literal_claim_found':any(literal_text(claim['claim']) in text for text in body),
            'references':refs,'assessment_is_model_judgment':True,'semantic_certification':False})
    return result
