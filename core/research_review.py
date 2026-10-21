"""Candidate identity and a clean, same-model review context."""
from hashlib import sha256
import json
from pathlib import Path
from .research_evidence import model_evidence


def fingerprint(candidate):
    return sha256(json.dumps(candidate,ensure_ascii=False,sort_keys=True,allow_nan=False).encode()).hexdigest()


def previous_findings(records):
    complete=[r for r in records.values() if r.get('status')=='COMPLETE' and r.get('attempts')]
    if not complete:return []
    latest=max(complete,key=lambda r:int(r['attempts'][-1].rsplit('/',1)[1]))
    # Carry adverse findings, not the previous reviewer's possibly wrong fix.
    return [{k:r[k] for k in ('section','claim','evidence')} for r in latest.get('issues',[])]


def review_messages(question, candidate, research, system, findings=()):
    instructions=(Path(__file__).resolve().parents[1]/'config/prompts/research_review.txt').read_text(encoding='utf-8')
    # The writer's self-checks are not independent evidence. Preserve the original
    # submission elsewhere; only remove this affirmation from the review view.
    view={key:value for key,value in candidate.items() if key!='review'}
    payload={'question':question,'candidate':view,'limitations':candidate.get('review',{}).get('limitations',[]),
             'evidence':model_evidence(research.evidence_index()),
             'methods':research.method_text,'previous_findings':list(findings)}
    return [{'role':'system','content':system+'\n\n'+instructions},
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
    from . import config
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
    return {'issues':value['issues'],'checks':value['checks'],'read_requests':reads,'semantic_certification':False}
