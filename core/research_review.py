"""Candidate identity and a clean, same-model review context."""
from hashlib import sha256
import json
from pathlib import Path


def fingerprint(candidate):
    return sha256(json.dumps(candidate,ensure_ascii=False,sort_keys=True,allow_nan=False).encode()).hexdigest()


def review_messages(question, candidate, research, system):
    instructions=(Path(__file__).resolve().parents[1]/'config/prompts/research_review.txt').read_text(encoding='utf-8')
    # The writer's self-checks are not independent evidence. Preserve the original
    # submission elsewhere; only remove this affirmation from the review view.
    view={key:value for key,value in candidate.items() if key!='review'}
    payload={'question':question,'candidate':view,'limitations':candidate.get('review',{}).get('limitations',[]),
             'evidence':review_evidence(research.evidence_index()),
             'methods':research.method_text}
    return [{'role':'system','content':system+'\n\n'+instructions},
            {'role':'user','content':json.dumps(payload,ensure_ascii=False)}]


def review_evidence(index):
    """Remove contained duplicate receipts without discarding unique evidence."""
    from copy import deepcopy
    index=deepcopy(index)
    for source in index.get('sources',{}).values():
        texts=list(dict.fromkeys(source.get('received',[])))
        source['received']=[text for text in texts if not any(text!=other and text in other for other in texts)]
    return index


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
    return {'issues':value['issues'],'checks':value['checks'],'semantic_certification':False}
