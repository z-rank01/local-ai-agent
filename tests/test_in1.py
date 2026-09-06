"""Offline IN1 contract tests; no provider network requests."""
from contextlib import closing
import asyncio
import hashlib
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import httpx
from core import config
from core.agent import Agent, AgentEvent, model_projection
from core.conversation_store import ConversationStore
from core.providers import CompatibleClient, CallBudget
from core.runtime import build_runtime
from bff.service import ChatSessionService
from bff.schemas import ChatRequest

CALL = {'id':'c1','type':'function','function':{'name':'code_exec','arguments':'{"code":"print(42)"}'}}

class StoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = ConversationStore(Path(self.tmp.name)/'db.sqlite')
        self.conv = self.store.create_conversation(title='test', model='qwen:mock')

    def test_protocol_replay_and_ui_do_not_duplicate(self):
        u = self.store.add_message(self.conv.id,role='user',content='hello',metadata={'cloud_safe':True})
        for message in [{'role':'assistant','content':'running','tool_calls':[CALL]}, {'role':'tool','tool_call_id':'c1','content':'42'}]:
            self.store.add_message(self.conv.id,role='protocol',content='',response_to_message_id=u.id, metadata={'message':message,'cloud_safe':True})
        self.store.add_message(self.conv.id,role='assistant',content='running',response_to_message_id=u.id)
        history = self.store.messages_as_dicts(self.conv.id,cloud=True)
        self.assertEqual([m['role'] for m in history],['user','assistant','tool'])
        self.assertEqual(history[-1]['tool_call_id'],'c1')
        self.assertTrue(json.loads(self.store.get_message(u.id).metadata)['cloud_safe'])

    def test_stop_repairs_missing_results(self):
        self.store.add_message(self.conv.id,role='protocol',content='',metadata={'message':{'role':'assistant','content':'','tool_calls':[CALL]}})
        self.store.add_message(self.conv.id,role='user',content='continue')
        history = self.store.messages_as_dicts(self.conv.id)
        self.assertEqual(history[1]['role'],'tool')
        self.assertIn('error',history[1]['content'])

    def test_legacy_local_not_sent_to_cloud(self):
        self.store.add_message(self.conv.id,role='user',content='private old file')
        self.store.add_message(self.conv.id,role='tool',content='private attachment')
        self.assertEqual(self.store.messages_as_dicts(self.conv.id,cloud=True),[])
        self.assertEqual(self.store.messages_as_dicts(self.conv.id)[1]['role'],'assistant')

    def test_migration_preserves_old_rows(self):
        path = Path(self.tmp.name)/'old.sqlite'
        from core.conversation_store import _SCHEMA
        with closing(sqlite3.connect(path)) as db, db:
            db.executescript(_SCHEMA)
            db.execute("INSERT INTO conversations VALUES ('old','old','ollama','a','a')")
            db.execute("INSERT INTO messages(id,conversation_id,role,content,created_at) VALUES ('m','old','user','original','a')")
        migrated = ConversationStore(path)
        self.assertEqual(migrated.get_message('m').content,'original')
        self.assertEqual(migrated.get_message('m').metadata,'')

    def test_budget_persists_and_does_not_exceed_limit(self):
        path = Path(self.tmp.name)/'calls.sqlite'
        CallBudget(path,2).reserve('test')
        CallBudget(path,2).reserve('test')
        with self.assertRaises(RuntimeError): CallBudget(path,2).reserve('test')
        with closing(sqlite3.connect(path)) as db, db: self.assertEqual(db.execute('select count(*) from calls').fetchone()[0],2)

    def test_nested_local_projection(self):
        value = {'local_result':'SECRET', 'items':[{'model_observation':{'fact':42},'local_result':'SECRET'}]}
        self.assertEqual(model_projection(value),{'items':[{'fact':42}]})

class ProviderTests(unittest.IsolatedAsyncioTestCase):
    async def client(self, chunks, status=200):
        spec = {'id':'qwen:mock','model':'mock','api_key_env':'IN1_FAKE_KEY','base_url':'https://example.invalid/v1'}
        client = CompatibleClient(spec,CallBudget('unused',0))
        await client._client.aclose()
        def handler(request):
            self.payload = json.loads(request.content)
            body = ''.join('data: '+json.dumps(c)+'\n\n' for c in chunks)
            return httpx.Response(status, text=body)
        client._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        self.addAsyncCleanup(client.close)
        return client

    async def test_chunked_calls_and_plain_stream(self):
        client = await self.client([
            {'choices':[{'delta':{'content':'Hello '}}]},
            {'choices':[{'delta':{'tool_calls':[{'index':0,'id':'c1','function':{'name':'file_','arguments':'{"path":'}}]}}]},
            {'choices':[{'delta':{'tool_calls':[{'index':0,'function':{'name':'read','arguments':'"/workspace/a"}'}}]},'finish_reason':'tool_calls'}]},
        ])
        with patch.dict('os.environ',{'IN1_FAKE_KEY':'dummy'}):
            events = [e async for e in client.chat_stream_with_tools([{'role':'user','content':'test'}])]
        self.assertEqual(events[0][0],'Hello ')
        self.assertEqual(events[-1][1]['tool_calls'][0]['function']['name'],'file_read')
        self.assertEqual(json.loads(events[-1][1]['tool_calls'][0]['function']['arguments']),{'path':'/workspace/a'})

    async def test_broken_stream_retains_partial_then_errors(self):
        client = await self.client([{'choices':[{'delta':{'content':'partial'}}]}])
        parts = []
        with patch.dict('os.environ',{'IN1_FAKE_KEY':'dummy'}):
            with self.assertRaisesRegex(RuntimeError,'中断'):
                async for token,_ in client.chat_stream_with_tools([]): parts.append(token)
        self.assertEqual(parts,['partial'])

    async def test_http_failure_has_no_key_or_fallback(self):
        client = await self.client([],401)
        with patch.dict('os.environ',{'IN1_FAKE_KEY':'dummy'}):
            with self.assertRaisesRegex(RuntimeError,'HTTP 401'):
                _ = [e async for e in client.chat_stream_with_tools([])]

class ServiceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.patches = [patch.object(config,'DB_PATH',Path(self.tmp.name)/'db'),patch.object(config,'WORKSPACE_PATH',Path(self.tmp.name)/'ws'),patch.object(config,'LOG_PATH',Path(self.tmp.name)/'audit'),patch.object(config,'WORKSPACE_CLOUD_ALLOWED',True)]
        for p in self.patches: p.start(); self.addCleanup(p.stop)
        self.runtime = build_runtime()
        self.addAsyncCleanup(self.runtime.close)
        self.service = ChatSessionService(self.runtime)
        self.seen = []
        parent = self
        class FakeModel:
            cloud=True
            model='qwen3.5-flash'
            async def chat_stream_with_tools(self,messages,tools=None):
                parent.seen.append(json.loads(json.dumps(messages)))
                yield 'hello',None
                yield '',{'role':'assistant','content':'hello'}
            async def close(self): pass
        self.runtime.models.clients['qwen:qwen3.5-flash'] = FakeModel()

    async def test_history_model_and_refresh(self):
        req = ChatRequest(message='hi',provider_id='qwen',model='qwen3.5-flash')
        events = [e async for e in self.service.stream_chat(req)]
        cid = events[0].conversation_id
        self.assertEqual(self.service.get_conversation(cid).model,'qwen:qwen3.5-flash')
        self.assertEqual([m.role for m in self.service.get_messages(cid)],['user','assistant'])
        self.assertEqual(self.service.get_messages(cid)[1].model,'qwen:qwen3.5-flash')
        _ = [e async for e in self.service.stream_chat(ChatRequest(conversation_id=cid,message='again'))]
        self.assertEqual([m['role'] for m in self.seen[-1] if m['role']!='system'],['user','assistant','user'])

    async def test_model_switch_routes_actual_client_and_edit(self):
        spec={'id':'alternate:mock','provider_id':'alternate','provider_name':'Test','model':'mock','kind':'cloud','base_url':'https://example.invalid','api_key_env':'IN1_FAKE_KEY'}
        self.runtime.models.specs.append(spec)
        parent=self
        class Alternate:
            cloud=True
            model='mock'
            async def chat_stream_with_tools(self,messages,tools=None):
                parent.alternate_seen=json.loads(json.dumps(messages))
                yield 'alternate',None
                yield '',{'role':'assistant','content':'alternate'}
            async def close(self):pass
        self.runtime.models.clients[spec['id']]=Alternate()
        events=[e async for e in self.service.stream_chat(ChatRequest(message='one',model='qwen:qwen3.5-flash'))]
        cid=events[0].conversation_id
        second=[e async for e in self.service.stream_chat(ChatRequest(conversation_id=cid,message='two',provider_id='alternate',model='mock'))]
        self.assertEqual(self.service.get_conversation(cid).model,spec['id'])
        self.assertIn('hello',json.dumps(self.alternate_seen))
        user=[m for m in self.service.get_messages(cid) if m.role=='user'][-1]
        _=[e async for e in self.service.edit_message_and_regenerate(cid,message_id=user.id,content='edited',provider_id='qwen',model='qwen3.5-flash')]
        self.assertEqual(self.service.get_messages(cid)[-1].model,'qwen:qwen3.5-flash')
        self.assertIn('edited',json.dumps(self.seen[-1]))

    async def test_unknown_model_fails_without_fallback(self):
        with self.assertRaises(Exception):
            _=[e async for e in self.service.stream_chat(ChatRequest(message='test',model='does-not-exist'))]
        self.assertFalse(self.seen)

    async def test_regeneration_reuses_tool_results_without_rerun(self):
        conv=self.runtime.store.create_conversation(title='test',model='qwen:qwen3.5-flash')
        user=self.runtime.store.add_message(conv.id,role='user',content='run',metadata={'cloud_safe':True})
        for message in [{'role':'assistant','content':'','tool_calls':[CALL]},{'role':'tool','tool_call_id':'c1','content':'42'}]:
            self.runtime.store.add_message(conv.id,role='protocol',metadata={'message':message,'cloud_safe':True},response_to_message_id=user.id)
        self.runtime.store.add_message(conv.id,role='tool',content='[ok] 42',response_to_message_id=user.id,metadata={'cloud_safe':True})
        self.runtime.store.add_message(conv.id,role='assistant',content='42',response_to_message_id=user.id,metadata={'cloud_safe':True})
        events=[e async for e in self.service.regenerate_chat(conv.id)]
        self.assertFalse(any(e.event=='tool.started' for e in events))
        self.assertEqual(self.seen[-1][-1]['role'],'tool')
        self.assertIn('42',self.seen[-1][-1]['content'])
        messages=self.service.get_messages(conv.id)
        self.assertEqual(messages[-1].version_number,2)
        self.assertEqual(messages[-1].version_count,2)

    async def test_cloud_search_never_reads_legacy_local_content(self):
        conv=self.runtime.store.create_conversation(title='SECRET',model='ollama:old')
        self.runtime.store.add_message(conv.id,role='user',content='SECRET')
        router=self.runtime.router
        router.cloud=True
        self.assertNotIn('SECRET',json.dumps(router._dispatch_local('conversation_read',{'conversation_id':conv.id})))
        self.assertEqual(router._dispatch_local('conversation_search',{'query':'SECRET'})['results'],[])

    async def test_repeated_request_id_not_executed_after_completion(self):
        request=ChatRequest(message='hello',model='qwen:qwen3.5-flash',request_id='repeat-request')
        first=[e async for e in self.service.stream_chat(request)]
        with self.assertRaisesRegex(Exception,'已受理'):
            _=[e async for e in self.service.stream_chat(request)]
        self.assertEqual(len(self.seen),1)
        self.assertEqual(len(self.service.get_messages(first[0].conversation_id)),2)

    async def test_duplicate_active_send_rejected_before_mutation(self):
        conv = self.runtime.store.create_conversation(title='test',model='qwen:qwen3.5-flash')
        first = self.service.stream_chat(ChatRequest(conversation_id=conv.id,message='first'))
        await anext(first)
        with self.assertRaisesRegex(Exception,'仍在运行'):
            _ = [e async for e in self.service.stream_chat(ChatRequest(conversation_id=conv.id,message='duplicate'))]
        self.assertEqual(self.runtime.store.get_messages(conv.id),[])
        await first.aclose()
        self.assertFalse(self.service._active_conversations)

    async def test_cancel_retains_partial_and_unlocks(self):
        stream = self.service.stream_chat(ChatRequest(message='hi',model='qwen:qwen3.5-flash'))
        cid = None
        async for event in stream:
            cid = event.conversation_id
            if event.event=='assistant.delta': break
        await stream.aclose()
        self.assertIn('中断',self.service.get_messages(cid)[-1].content)
        self.assertFalse(self.service._active_conversations)

class AgentTests(unittest.IsolatedAsyncioTestCase):
    def agent(self, replies, result=None, max_rounds=3):
        parent=self
        class Model:
            cloud=True
            async def chat_stream_with_tools(self,messages,tools=None):
                parent.inputs.append(json.loads(json.dumps(messages)))
                reply = replies.pop(0)
                yield '',reply
        class Router:
            async def dispatch_stream(self,*args):
                parent.runs += 1
                yield {'event':'result','result':result}
        async def process(messages): return messages
        self.inputs=[]; self.runs=0
        return Agent(llm=Model(),router=Router(), registry=SimpleNamespace(get_definitions=lambda **kw:[{'function':{'name':'code_exec'}}]),
            audit=SimpleNamespace(record=lambda *a:None),context_mgr=SimpleNamespace(process=process),
            prompt_builder=SimpleNamespace(build=lambda **kw:'test'),max_rounds=max_rounds)

    async def test_failed_tool_observation_and_protocol_ids(self):
        agent=self.agent([{'role':'assistant','content':'','tool_calls':[CALL]}, {'role':'assistant','content':'tool failed'}],{'exit_code':1,'stderr':'boom'})
        with patch.object(config,'WORKSPACE_CLOUD_ALLOWED',True): events=[e async for e in agent.run([{'role':'user','content':'go'}])]
        end=next(e for e in events if e.kind=='tool_end')
        self.assertEqual(end.data['status'],'error')
        self.assertEqual(self.inputs[1][-1]['tool_call_id'],'c1')
        self.assertIn('boom',self.inputs[1][-1]['content'])

    async def test_invalid_arguments_never_execute(self):
        bad=json.loads(json.dumps(CALL)); bad['function']['arguments']='not json'
        agent=self.agent([{'role':'assistant','content':'','tool_calls':[bad]}, {'role':'assistant','content':'corrected'}])
        with patch.object(config,'WORKSPACE_CLOUD_ALLOWED',True): events=[e async for e in agent.run([{'role':'user','content':'go'}])]
        self.assertEqual(self.runs,0)
        self.assertEqual(next(e for e in events if e.kind=='tool_end').data['status'],'error')

    async def test_final_round_does_not_execute_rogue_calls(self):
        agent=self.agent([{'role':'assistant','content':'','tool_calls':[CALL]}],max_rounds=1)
        with patch.object(config,'WORKSPACE_CLOUD_ALLOWED',True): events=[e async for e in agent.run([{'role':'user','content':'go'}])]
        self.assertEqual(self.runs,0)
        self.assertEqual(events[-1].kind,'error')

    async def test_unapproved_workspace_cloud_blocks_tools(self):
        agent=self.agent([{'role':'assistant','content':'','tool_calls':[CALL]}, {'role':'assistant','content':'unavailable'}])
        with patch.object(config,'WORKSPACE_CLOUD_ALLOWED',False): _=[e async for e in agent.run([{'role':'user','content':'go'}])]
        self.assertEqual(self.runs,0)

class CompactTests(unittest.IsolatedAsyncioTestCase):
    async def test_compact_preserves_entire_recent_tool_turn(self):
        from core.context_manager import ContextManager
        class Model:
            async def chat(self,*args): return 'old summary'
        manager=ContextManager(context_window=1000,compact_threshold=.6,llm=Model())
        messages=[{'role':'system','content':'system'}]
        for i in range(10):
            messages.extend([{'role':'user','content':str(i)},{'role':'assistant','content':'','tool_calls':[CALL]}, {'role':'tool','tool_call_id':'c1','content':'42'},{'role':'assistant','content':'ok'}])
        result=await manager.auto_compact(messages)
        self.assertEqual(result[2]['role'],'user')
        pending=set()
        for message in result:
            if message.get('tool_calls'):pending.update(t['id'] for t in message['tool_calls'])
            if message['role']=='tool':self.assertIn(message['tool_call_id'],pending);pending.remove(message['tool_call_id'])
        self.assertFalse(pending)

class EditTests(unittest.TestCase):
    def setUp(self):
        import sys
        sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'skills/files'))
        from file_ops import FileOps
        from path_guard import PathGuard
        self.tmp=tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)
        self.ops=FileOps(PathGuard(str(self.root)))

    def test_crlf_long_file_two_edits(self):
        path=self.root/'long.txt'; original=('first\r\n'*2000+'中文：3\r\nlast\r\n').encode('utf-8');path.write_bytes(original)
        before=self.ops.read(str(path))
        result=self.ops.edit(str(path),'中文：3','中文：4',before['sha256'])
        self.assertEqual(path.read_bytes(),original.replace('中文：3'.encode(),'中文：4'.encode()))
        self.ops.edit(str(path),'中文：4','中文：5',result['sha256'])
        self.assertEqual(path.read_bytes(),original.replace('中文：3'.encode(),'中文：5'.encode()))

    def test_gbk_rejected_without_changing_bytes(self):
        path=self.root/'gbk.txt';original='中文：3'.encode('gbk');path.write_bytes(original)
        with self.assertRaisesRegex(ValueError,'UTF-8'):self.ops.edit(str(path),'中文：3','中文：4',hashlib.sha256(original).hexdigest())
        self.assertEqual(path.read_bytes(),original)

    def test_no_match_and_stale_hash_leave_file_intact(self):
        path=self.root/'a.txt';path.write_bytes(b'abc')
        for old,hash_ in [('x',hashlib.sha256(b'abc').hexdigest()),('a','bad')]:
            with self.assertRaises(ValueError):self.ops.edit(str(path),old,'new',hash_)
        self.assertEqual(path.read_bytes(),b'abc')

if __name__=='__main__': unittest.main()
