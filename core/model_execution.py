"""Frozen conversation model calls shared by skill work and local delegation."""
import asyncio
import copy
import hashlib
import json

import httpx


def freeze_profile(spec, conversation_id, workspace_id, cloud_read, run_id=None):
    return {'version': 1, 'conversation_id': conversation_id, 'workspace_id': workspace_id,
            'run_id': run_id, 'cloud_read': bool(cloud_read),
            'model': {k: spec[k] for k in ('id','provider_id','model','kind','base_url','options','context_window') if k in spec}}


def resolve_profile(runtime, profile):
    if not isinstance(profile, dict) or profile.get('version') != 1:
        raise ValueError('旧模型流程仅可查看；请在会话中创建新任务')
    conversation = runtime.store.get_conversation(profile['conversation_id'])
    if conversation is None or conversation.workspace_id != profile['workspace_id']:
        raise ValueError('任务缺少有效的所属会话与工作区')
    frozen = profile['model']
    spec, client = runtime.models.resolve(model=frozen['id'])
    if any(spec.get(k) != frozen.get(k) for k in ('provider_id','model','kind','base_url')):
        raise ValueError('冻结模型连接配置已变化；未替换模型')
    if spec['kind'] == 'cloud':
        from .workspaces import WorkspaceManager
        from . import config
        if not profile['cloud_read'] or not WorkspaceManager(runtime.store,config.WORKSPACE_PATH).get(profile['workspace_id'])['cloud_read']:
            raise PermissionError('本任务没有有效的云端读取授权')
    spec = {**spec, 'options': frozen.get('options', {})}
    client = copy.copy(client)
    if spec['kind'] == 'cloud':
        client.spec = spec
    else:
        client.think = spec['options'].get('think')
    return spec, client


async def invoke(runtime, profile, prompt, material, *, observe=None):
    """One call, no retry or fallback. Receipts exclude prose and reasoning."""
    spec, client = resolve_profile(runtime, profile)
    if spec['kind']=='cloud':
        from .providers import read_key
        if not read_key(spec['api_key_env']):
            raise ValueError('所选会话模型尚未配置密钥，未发起请求')
    messages = [{'role':'system','content':prompt}, {'role':'user','content':
        material if isinstance(material,str) else json.dumps(material,ensure_ascii=False,allow_nan=False)}]
    params = {'model':spec['id'], 'options':spec['options']}
    digest = lambda v: hashlib.sha256(json.dumps(v,sort_keys=True,ensure_ascii=False).encode()).hexdigest()
    ledger = runtime.models.budget
    call_id = ledger.begin_research(spec['id'],digest(profile),digest(messages),params)
    native = {}
    def event(name, data):
        if name == 'native_model_result': native.update(data)
        if name == 'native_model_request' and data.get('parameters'):
            result['model_params']={**data['parameters'],'model':spec['id']}
        safe = {**data, 'call_id':call_id, 'model':spec['id'],
                'session_id':profile['conversation_id']}
        runtime.audit.record(name,safe)
        if observe: observe(name,safe)
    result = {'status':'UNKNOWN', 'text':'', 'model_params':params, 'ledger_call_id':call_id,
              'execution_hash':digest(profile), 'usage':None, 'usage_status':'NOT_REPORTED'}
    try:
        response = None
        if spec['kind'] == 'local':
            event('native_model_request',{'input_hash':digest(messages),'message_count':2})
            payload = {'model':spec['model'],'messages':messages,'stream':True}
            if client.think is not None: payload['think']=client.think
            parts=[]; terminal=None
            async with client._client.stream('POST',spec['base_url'].rstrip('/')+'/api/chat',json=payload) as stream:
                stream.raise_for_status()
                async for line in stream.aiter_lines():
                    if not line.strip(): continue
                    packet=json.loads(line)
                    if packet.get('error'): raise RuntimeError('本地模型返回错误')
                    parts.append(packet.get('message',{}).get('content',''))
                    if packet.get('done'): terminal=packet
            if terminal is None or terminal.get('done_reason') not in ('stop','length'):
                raise RuntimeError('本地模型缺少已确认终态')
            usage={k:terminal[k] for k in ('prompt_eval_count','eval_count') if k in terminal}
            result.update(status='TRUNCATED' if terminal['done_reason']=='length' else 'COMPLETE',
                          text=''.join(parts),finish_reason=terminal['done_reason'],
                          response_model=terminal.get('model'),usage=usage or None,
                          usage_status='REPORTED' if usage else 'NOT_REPORTED')
            event('native_model_result',{'result_status':result['status'],
                'finish_reason':result['finish_reason'],'usage':result['usage']})
        else:
            # This call is already booked in the research ledger. Do not also
            # consume the ordinary chat reservation for the same HTTP request.
            from .providers import CallBudget
            client.budget = CallBudget(ledger.path,limit=0)
            parts=[]; thinking=False
            async for token, message in client.chat_stream_with_tools(messages,None,request_observer=event):
                if token.strip()=='<think>': thinking=True
                elif token.strip()=='</think>': thinking=False
                elif not thinking: parts.append(token)
                if message is not None: response=message
            if response is None: raise RuntimeError('模型没有最终回答')
            result.update(status='COMPLETE',text=response.get('content',''),
                          finish_reason=native.get('finish_reason'),response_id=native.get('response_id'),
                          response_model=native.get('response_model'),usage=native.get('usage'),
                          usage_status='REPORTED' if native.get('usage') else 'NOT_REPORTED')
        if not result['text'].strip():
            result.update(status='FAILED',error='EMPTY_RESULT')
    except asyncio.CancelledError:
        result.update(error='REQUEST_CANCELLED')
        raise
    except (httpx.HTTPError,RuntimeError,ValueError,KeyError,TypeError) as exc:
        result.update(error=type(exc).__name__,failure_category=native.get('failure_category'))
        if isinstance(exc,(httpx.ConnectError,httpx.ConnectTimeout)):
            result.update(status='FAILED',error='MODEL_UNAVAILABLE')
        if native.get('result_status') in ('COMPLETE','FAILED','TRUNCATED'):
            result['status']=native['result_status']
        if native.get('result_status')=='TRUNCATED':
            result.update(text=''.join(parts),finish_reason=native.get('finish_reason'))
    finally:
        ledger.finish_research(call_id,result)
    return result
