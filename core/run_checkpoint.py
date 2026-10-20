"""Explicit continuation using existing evidence; never serialize model reasoning."""
from hashlib import sha256
import json
from . import config


def environment(spec, snapshot, method):
    prompts = {p.name: sha256(p.read_bytes()).hexdigest()
               for p in (config.PROMPTS_DIR/'modules').glob('*') if p.is_file()}
    return {'format': 1, 'model': {k: spec[k] for k in
            ('id','provider_id','model','base_url','options') if k in spec},
            'workspace_id': snapshot.id, 'workspace_version': snapshot.record['version'],
            'roots': {k:str(v) for k,v in snapshot.roots.items()}, 'cloud_read': snapshot.record['cloud_read'],
            'cloud_write': snapshot.record['cloud_write'], 'method': method, 'prompts': prompts}


def capture(agent, messages, frozen, response_id, archive):
    def save(message):
        clean = {k:v for k,v in message.items() if k not in ('reasoning_content','thinking')}
        return archive(clean)
    research = getattr(agent.router, 'research', None)
    state = None
    if research is not None and research.record is not None:
        state = {k:getattr(research,k) for k in ('record','sources','trace','draft_count','publication_attempts')}
        state['methods'] = sorted(research.methods)
        state['method_fingerprints'] = research.method_fingerprints
        state['milestones'] = sorted(research._milestones)
        state['steps_without_progress'] = research.steps_without_progress
        state.update({key:getattr(research,key) for key in
                      ('pending_evidence','evidence_requests','evidence_notes','method_text')})
        state['read_text'] = {key:[save({'role':'tool','content':text}) for text in spans]
                              for key,spans in research.read_text.items()}
    return {'format':1, 'safe':True, 'blocker':'', 'frozen':frozen,
            'response_to_message_id':response_id,
            'messages':[save(m) for m in messages], 'research':state,
            'evidence_ids':sorted(agent.context_mgr.evidence_ids)}


def load_message(store, conversation_id, scope, evidence_id):
    with store._connect() as db:
        row = db.execute('SELECT content,metadata FROM task_evidence WHERE '
                         'conversation_id=? AND scope=? AND id=?',
                         (conversation_id,scope,evidence_id)).fetchone()
    if row is None:
        raise ValueError('检查点引用的证据不存在')
    return {**json.loads(row['metadata']), 'content':row['content']}


def continuation_messages(checkpoint, load):
    result = []
    for evidence_id in checkpoint['messages']:
        message = load(evidence_id)
        if message.get('tool_calls'):
            message = {'role':'system', 'content':'此前工具请求记录（不自动执行）：'+json.dumps(message,ensure_ascii=False)}
        elif message['role'] == 'tool':
            message = {'role':'system','content':'此前实际收到的工具回执：'+json.dumps(message,ensure_ascii=False)}
        result.append(message)
    result.append({'role':'system','content':
        '用户明确要求从已保存检查点继续同一研究。原工具回执和未执行请求已保留；'
        '不重复已完成步骤，不重发UNKNOWN，不改变模型或原问题。根据剩余缺口继续，发布仍须实际成功。'})
    return result


def restore_research(research, state, load):
    if state is None:
        return
    for key in ('record','sources','trace','draft_count','publication_attempts'):
        setattr(research,key,state[key])
    research.record = {**research.record,'status':'IN_PROGRESS'}
    research.methods = set(state['methods'])
    research.method_fingerprints = state.get('method_fingerprints',{})
    research._milestones = {tuple(item) for item in state.get('milestones', [])}
    research._observed_milestones = len(research._milestones)
    research.steps_without_progress = state.get('steps_without_progress', 0)
    for key in ('pending_evidence','evidence_requests','evidence_notes','method_text'):
        setattr(research,key,state.get(key, [] if key == 'pending_evidence' else {}))
    research.read_text = {key:[load(evidence_id)['content'] for evidence_id in spans]
                          for key,spans in state['read_text'].items()}
