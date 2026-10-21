import asyncio
import json
import unittest
from contextlib import closing
from unittest.mock import patch, Mock, AsyncMock
import httpx
from fastapi import HTTPException
from core.model_execution import freeze_profile, invoke
from core.local_agent import delegate
from core.providers import CompatibleClient, CallBudget
from plugins.finance.generation import generate
from tests import test_in1


class ModelExecutionTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        from pathlib import Path
        directory=Path(__file__).resolve().parents[1]/'data/tmp'
        directory.mkdir(exist_ok=True)
        temporary=patch('tempfile.tempdir',str(directory))
        temporary.start();self.addCleanup(temporary.stop)
        await test_in1.ServiceTests.asyncSetUp(self)
        self.runtime.models.budget=CallBudget(Path(self.tmp.name)/'model-calls.sqlite')

    def profile(self,model='qwen:qwen3.5-flash'):
        conv=self.service.create_conversation('execution',model)
        spec,_=self.service._execution_model(self.runtime.store.get_conversation(conv.id))
        return freeze_profile(spec,conv.id,'default',True)

    async def cloud(self,handler):
        spec,_=self.runtime.models.resolve(model='qwen:qwen3.5-flash')
        client=CompatibleClient(spec,self.runtime.models.budget)
        await client._client.aclose()
        client._client=httpx.AsyncClient(transport=httpx.MockTransport(handler))
        self.runtime.models.clients[spec['id']]=client

    async def test_cloud_uses_frozen_choice_and_records_one_call(self):
        seen=[]
        def respond(request):
            seen.append(json.loads(request.content))
            data={'id':'response-test','model':'qwen3.5-flash','choices':[
                {'index':0,'delta':{'content':'{"result":"ok"}'},'finish_reason':'stop'}]}
            return httpx.Response(200,text='data: '+json.dumps(data)+'\n\ndata: [DONE]\n\n')
        await self.cloud(respond); profile=self.profile()
        self.service.update_conversation(profile['conversation_id'],model='ollama:gemma4:26b')
        with patch('core.providers.read_key',return_value='fixture-key'):
            result=await invoke(self.runtime,profile,'只输出JSON','公开材料')
        self.assertEqual(result['status'],'COMPLETE')
        self.assertEqual(seen[0]['model'],'qwen3.5-flash')
        self.assertEqual(len(seen),1)
        self.assertEqual(self.runtime.models.budget.status()['used'],1)

    async def test_unknown_is_recorded_without_retry(self):
        seen=[]
        def respond(request):
            seen.append(request)
            return httpx.Response(200,text='data: {"choices":[{"delta":{"content":"partial"}}]}\n\n')
        await self.cloud(respond)
        from harness.infrastructure.model_ledger import RecordedRequest
        profile = self.profile()
        with patch('core.providers.read_key',return_value='fixture-key'):
            result=await invoke(self.runtime,profile,'test','public',request_id='stable-attempt')
            # A new gateway/ledger object after a restart must still refuse replay.
            self.runtime.models.budget = CallBudget(self.runtime.models.budget.path)
            with self.assertRaises(RecordedRequest) as repeated:
                await invoke(self.runtime,profile,'test','public',request_id='stable-attempt')
            self.assertEqual(repeated.exception.status, 'UNKNOWN')
            self.assertFalse(repeated.exception.conflict)
            with self.assertRaises(RecordedRequest) as changed:
                await invoke(self.runtime,profile,'test','changed input',request_id='stable-attempt')
            self.assertTrue(changed.exception.conflict)
        self.assertEqual(result['status'],'UNKNOWN');self.assertEqual(len(seen),1)
        self.assertIsNone(result['usage'])
        self.assertEqual(self.runtime.models.budget.status()['used'], 1)

    async def test_private_material_does_not_reach_cloud(self):
        with self.assertRaises(HTTPException) as raised:
            await generate(self.runtime,{'execution_profile':self.profile(),'execution_hash':'a'*64,
                'prompt':'test','material':{'account':{'cash':10}}})
        self.assertEqual(raised.exception.status_code,422)
        self.assertEqual(self.seen,[])

    async def test_local_rejection_is_known_failure_not_unknown(self):
        _,client=self.runtime.models.resolve(model='ollama:gemma4:26b')
        await client._client.aclose()
        client._client=httpx.AsyncClient(transport=httpx.MockTransport(
            lambda request:httpx.Response(404,json={'error':'model not found'})))
        result=await invoke(self.runtime,self.profile('ollama:gemma4:26b'),'test','public')
        self.assertEqual(result['status'],'FAILED')
        self.assertEqual(result['http_status'],404)

    async def test_local_delegate_has_no_tools_and_checks_evidence_before_call(self):
        profile=self.profile(); seen=[]
        spec,client=self.runtime.models.resolve(model='ollama:gemma4:26b')
        await client._client.aclose()
        def respond(request):
            seen.append(json.loads(request.content))
            return httpx.Response(200,text=json.dumps({'model':'gemma4:26b','done':True,
                'done_reason':'stop','message':{'content':'辅助意见'}})+'\n')
        client._client=httpx.AsyncClient(transport=httpx.MockTransport(respond))
        def read(key):
            if key!='registered':raise ValueError('证据未登记')
            return {'content':'已授权原文'}
        result=await delegate(self.runtime,profile,{'task':'整理材料','evidence_ids':['registered']},read,lambda *args:None)
        self.assertEqual(result['delegate_model'],'ollama:gemma4:26b')
        self.assertEqual(result['text'],'辅助意见');self.assertNotIn('tools',seen[0])
        with self.assertRaises(ValueError):
            await delegate(self.runtime,profile,{'task':'整理','evidence_ids':['private']},read,lambda *args:None)
        self.assertEqual(len(seen),1)
        with self.assertRaisesRegex(ValueError,'不递归'):
            await delegate(self.runtime,self.profile('ollama:gemma4:26b'),{'task':'test'},read,lambda *args:None)

    async def test_revoked_grant_rejects_before_local_delegation(self):
        profile=self.profile();profile['cloud_read']=False
        with self.assertRaises(PermissionError):
            await delegate(self.runtime,profile,{'task':'test'},lambda _:None,lambda *args:None)

    async def test_local_unknown_blocks_safe_continuation(self):
        from types import SimpleNamespace
        from core.agent import Agent
        profile=self.profile()
        _,client=self.runtime.models.resolve(model='ollama:gemma4:26b')
        await client._client.aclose()
        client._client=httpx.AsyncClient(transport=httpx.MockTransport(
            lambda request:httpx.Response(200,text='{"message":{"content":"partial"}}\n')))
        agent=Agent(llm=object(),router=SimpleNamespace(models_runtime=self.runtime,
            execution_profile=profile,_policy=SimpleNamespace(check=lambda *args:None)),
            registry=None,audit=self.runtime.audit,context_mgr=SimpleNamespace(read_evidence=lambda _:None),
            prompt_builder=None)
        agent.run_control=SimpleNamespace(boundary=AsyncMock(),begin_step=Mock(),end_step=Mock())
        events=[e async for e in agent._execute_tools([{'id':'local-test','function':{
            'name':'local_agent','arguments':{'task':'test'}}}],'test',[],allowed={'local_agent'})]
        self.assertTrue(agent.local_agent_unknown)
        agent.run_control.end_step.assert_called_once_with(confirmed=False)
        self.assertEqual(events[-1].data['status'],'error')

    async def test_cancelled_call_keeps_unknown_receipt(self):
        entered=asyncio.Event()
        class Waiting(httpx.AsyncByteStream):
            async def __aiter__(self):
                entered.set();await asyncio.Event().wait()
                yield b''
        _,client=self.runtime.models.resolve(model='ollama:gemma4:26b')
        await client._client.aclose()
        client._client=httpx.AsyncClient(transport=httpx.MockTransport(
            lambda request:httpx.Response(200,stream=Waiting())))
        task=asyncio.create_task(invoke(self.runtime,self.profile('ollama:gemma4:26b'),'test','material'))
        await asyncio.wait_for(entered.wait(),5);task.cancel()
        with self.assertRaises(asyncio.CancelledError):await task
        import sqlite3
        with closing(sqlite3.connect(self.runtime.models.budget.path)) as db:
            self.assertEqual(db.execute('SELECT status FROM research_receipts').fetchone()[0],'UNKNOWN')

    async def test_stock_node_stop_cancels_actual_call_without_outbound_ids(self):
        from plugins.finance.generation import stop_job
        entered=asyncio.Event()
        class Waiting(httpx.AsyncByteStream):
            async def __aiter__(self):
                entered.set();await asyncio.Event().wait()
                yield b''
        _,client=self.runtime.models.resolve(model='ollama:gemma4:26b')
        await client._client.aclose()
        def response(request):
            self.assertNotIn('node-stop-test',request.content.decode())
            return httpx.Response(200,stream=Waiting())
        client._client=httpx.AsyncClient(transport=httpx.MockTransport(response))
        task=asyncio.create_task(generate(self.runtime,{
            'execution_profile':self.profile('ollama:gemma4:26b'),'execution_hash':'a'*64,
            'prompt':'test','material':'public','node':{'job_id':'node-stop-test','request_id':'one'}}))
        await asyncio.wait_for(entered.wait(),5)
        self.assertEqual((await stop_job('node-stop-test'))['cancelled_nodes'],1)
        self.assertEqual((await task)['status'],'UNKNOWN')
