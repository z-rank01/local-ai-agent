"""Explicit continuation using existing evidence; never serialize model reasoning."""
from hashlib import sha256
import json
from . import config


def environment(spec, snapshot, method):
    from harness.runtime.call_policies import contribution_versions
    prompts = {p.name: sha256(p.read_bytes()).hexdigest()
               for p in (config.PROMPTS_DIR/'modules').glob('*') if p.is_file()}
    from harness.capabilities.prompts import load_prompts
    contributions, errors = load_prompts(config._PROJECT_ROOT / 'plugins')
    prompts.update({'plugin:' + item.identity: item.fingerprint for item in contributions})
    prompts.update({'plugin-error:' + identity: sha256(error.encode()).hexdigest()
                    for identity, error in errors.items()})
    return {'format': 1, 'model': {k: spec[k] for k in
            ('id','provider_id','model','base_url','options') if k in spec},
            'workspace_id': snapshot.id, 'workspace_version': snapshot.record['version'],
            'roots': {k:str(v) for k,v in snapshot.roots.items()}, 'cloud_read': snapshot.record['cloud_read'],
            'cloud_write': snapshot.record['cloud_write'], 'method': method, 'prompts': prompts,
            'call_contributions': contribution_versions(config._PROJECT_ROOT / 'plugins')}


def capture(agent, messages, frozen, response_id, archive):
    def save(message):
        clean = {k:v for k,v in message.items() if k not in ('reasoning_content','thinking')}
        return archive(clean)
    from harness.runtime.turns import NullTurn
    turn = getattr(agent, 'turn', None) or NullTurn()
    state = turn.checkpoint_state(save)
    return {'format':1, 'safe':True, 'blocker':'', 'frozen':frozen,
            'response_to_message_id':response_id,
            'messages':[save(m) for m in messages], 'turn':state,
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
