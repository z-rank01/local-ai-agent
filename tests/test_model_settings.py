"""Offline settings and live-stream contract checks, without real model calls."""
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import httpx
from core import config, model_settings
from core.llm_client import LLMClient
from core.providers import CompatibleClient, CallBudget, thinking_capability
from bff.schemas import ChatRequest
from tests import test_in1

class SettingsTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        for p in [patch.object(model_settings, 'settings_path', lambda: Path(self.tmp.name)/'settings.json'),
                  patch.object(config, 'WORKSPACE_PATH', Path(self.tmp.name)/'workspace'),
                  patch.object(config, 'WORKSPACE_CLOUD_ALLOWED', False)]:
            p.start(); self.addCleanup(p.stop)

    async def test_workspace_isolation_and_explicit_revocation(self):
        spec = {'id':'qwen:test','options':{'enable_thinking':False}}
        self.assertFalse(model_settings.workspace_allowed())
        model_settings.update_settings(spec['id'], thinking=True, workspace=True)
        self.assertTrue(model_settings.workspace_allowed())
        self.assertTrue(model_settings.thinking_enabled(spec))
        with patch.object(config, 'WORKSPACE_PATH', Path(self.tmp.name)/'other'):
            self.assertFalse(model_settings.workspace_allowed())
        model_settings.update_settings(spec['id'], workspace=False)
        with patch.object(config, 'WORKSPACE_CLOUD_ALLOWED', True):
            self.assertFalse(model_settings.workspace_allowed())
        self.assertTrue(model_settings.thinking_enabled(spec))

    def test_thinking_tristate_and_budget(self):
        spec = {'id':'qwen:tristate','options':{'enable_thinking':True}}
        self.assertIsNone(model_settings.thinking_setting(spec))
        self.assertTrue(model_settings.thinking_enabled(spec))
        model_settings.update_settings(spec['id'], thinking=False)
        self.assertFalse(model_settings.thinking_setting(spec))
        self.assertFalse(model_settings.thinking_enabled(spec))
        model_settings.update_settings(spec['id'], thinking=None)
        self.assertIsNone(model_settings.thinking_setting(spec))
        self.assertTrue(model_settings.thinking_enabled(spec))
        model_settings.update_settings(spec['id'], budget=4096)
        self.assertEqual(model_settings.thinking_budget(spec['id']), 4096)
        model_settings.update_settings(spec['id'], budget=0)
        self.assertIsNone(model_settings.thinking_budget(spec['id']))
        model_settings.update_settings(spec['id'], budget=None)
        self.assertIsNone(model_settings.thinking_budget(spec['id']))

    def test_thinking_capability_mapping(self):
        self.assertEqual(thinking_capability({'kind':'local','provider_id':'ollama','model':'gemma4:26b'}), 'switch')
        self.assertEqual(thinking_capability({'kind':'cloud','provider_id':'qwen','model':'qwen3.8-flash'}), 'switch_budget')
        self.assertIsNone(thinking_capability({'kind':'cloud','provider_id':'qwen','model':'ZHIPU/GLM-5.3'}))
        self.assertIsNone(thinking_capability({'kind':'cloud','provider_id':'qwen','model':'deepseek-v4-pro'}))
        self.assertIsNone(thinking_capability({'kind':'cloud','provider_id':'other','model':'qwen3-clone'}))

    async def test_thinking_endpoint_validation_and_clear(self):
        from bff.app import app
        qwen = {'id':'qwen:qwen3.8-flash','kind':'cloud','provider_id':'qwen','model':'qwen3.8-flash'}
        hosted = {'id':'qwen:ZHIPU/GLM-5.3','kind':'cloud','provider_id':'qwen','model':'ZHIPU/GLM-5.3'}
        service = SimpleNamespace(_active_conversations=set())
        runtime = SimpleNamespace(models=SimpleNamespace(specs=[qwen, hosted]))
        base = {'workspace_path':str(config.WORKSPACE_PATH.resolve())}
        with patch('bff.app.get_runtime', return_value=runtime), patch('bff.app.get_chat_service', return_value=service):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app, client=('127.0.0.1',1234)), base_url='http://test') as client:
                ok = await client.patch('/api/model-settings', json={**base,'model_id':qwen['id'],'thinking_budget':4096})
                self.assertEqual(ok.status_code,200)
                self.assertEqual(model_settings.thinking_budget(qwen['id']),4096)
                denied = await client.patch('/api/model-settings', json={**base,'model_id':hosted['id'],'thinking_enabled':True})
                self.assertEqual(denied.status_code,422)
                denied_budget = await client.patch('/api/model-settings', json={**base,'model_id':hosted['id'],'thinking_budget':4096})
                self.assertEqual(denied_budget.status_code,422)
                out_of_range = await client.patch('/api/model-settings', json={**base,'model_id':qwen['id'],'thinking_budget':8})
                self.assertEqual(out_of_range.status_code,422)
                cleared = await client.patch('/api/model-settings', json={**base,'model_id':qwen['id'],'thinking_budget':None})
                self.assertEqual(cleared.status_code,200)
                self.assertIsNone(model_settings.thinking_budget(qwen['id']))
                reset = await client.patch('/api/model-settings', json={**base,'model_id':qwen['id'],'thinking_enabled':None})
                self.assertEqual(reset.status_code,200)
                self.assertIsNone(model_settings.thinking_setting(qwen))

    async def test_endpoint_rejects_other_origin_workspace_and_active_turn(self):
        from bff.app import app
        spec = {'id':'qwen:test','kind':'cloud','options':{'enable_thinking':False}}
        service = SimpleNamespace(_active_conversations=set())
        runtime = SimpleNamespace(models=SimpleNamespace(specs=[spec]))
        payload = {'model_id':spec['id'], 'workspace_path':str(config.WORKSPACE_PATH.resolve()), 'workspace_cloud_allowed':True}
        with patch('bff.app.get_runtime', return_value=runtime), patch('bff.app.get_chat_service', return_value=service):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app, client=('127.0.0.1',1234)), base_url='http://test') as client:
                self.assertEqual((await client.patch('/api/model-settings',json=payload,headers={'origin':'https://untrusted.invalid'})).status_code,403)
                self.assertEqual((await client.patch('/api/model-settings',json={**payload,'workspace_path':'other'})).status_code,409)
                service._active_conversations.add('running')
                self.assertEqual((await client.patch('/api/model-settings',json=payload)).status_code,409)
                service._active_conversations.clear()
                self.assertFalse(model_settings.workspace_allowed())
                self.assertEqual((await client.patch('/api/model-settings',json=payload)).status_code,200)
                self.assertTrue(model_settings.workspace_allowed())

class StreamSettingsTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = test_in1.ServiceTests.asyncSetUp
    async def test_settings_control_transport_and_tool_timeline(self):
        with patch.object(model_settings, 'settings_path', lambda: Path(self.tmp.name)/'settings.json'):
            spec = next(s for s in self.runtime.models.specs if s['provider_id']=='qwen')
            payloads=[]
            def handler(request):
                body=json.loads(request.content); payloads.append(body)
                if body.get('tools') and len(payloads)==2:
                    delta={'reasoning_content':'test reasoning','tool_calls':[{'index':0,**test_in1.CALL}]}
                    finish='tool_calls'
                else:
                    delta={'content':'test reply'}; finish='stop'
                return httpx.Response(200,text='data: '+json.dumps({'choices':[{'delta':delta,'finish_reason':finish}]})+'\n\ndata: [DONE]\n\n')
            client=CompatibleClient(spec,CallBudget(Path(self.tmp.name)/'calls',10))
            await client._client.aclose()
            client._client=httpx.AsyncClient(transport=httpx.MockTransport(handler))
            self.runtime.models.clients[spec['id']]=client
            async def fake_dispatch(*args):
                yield {'event':'result','result':{'stdout':'42'}}
            with patch('core.providers.read_key',return_value='fake'), patch.object(self.runtime.router,'dispatch_stream',fake_dispatch):
                model_settings.update_settings(spec['id'],thinking=False,workspace=False)
                events=[e async for e in self.service.stream_chat(ChatRequest(message='list files',model=spec['id']))]
                self.assertNotIn('tools',payloads[0])
                self.assertFalse(payloads[0]['enable_thinking'])
                self.assertIn('模型设置',payloads[0]['messages'][0]['content'])
                model_settings.update_settings(spec['id'],thinking=True,workspace=True)
                events=[e async for e in self.service.stream_chat(ChatRequest(message='calculate',model=spec['id']))]
                self.assertTrue(payloads[1]['tools'])
                self.assertTrue(payloads[1]['enable_thinking'])
                names=[e.event for e in events]
                self.assertIn('reasoning.delta',names)
                self.assertIn('tool.started',names)
                self.assertIn('tool.completed',names)
                self.assertTrue(any(m.thinking for m in self.service.get_messages(events[0].conversation_id)))
                model_settings.update_settings(spec['id'],workspace=False)
                _=[e async for e in self.service.stream_chat(ChatRequest(message='stop tools',model=spec['id']))]
                self.assertNotIn('tools',payloads[-1])
                self.assertTrue(payloads[-1]['enable_thinking'])

class ThinkingInjectionTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = test_in1.ServiceTests.asyncSetUp

    def mock_client(self, spec, payloads):
        def handler(request):
            payloads.append(json.loads(request.content))
            return httpx.Response(200,text='data: '+json.dumps({'choices':[{'delta':{'content':'ok'},'finish_reason':'stop'}]})+'\n\ndata: [DONE]\n\n')
        client=CompatibleClient(spec,CallBudget(Path(self.tmp.name)/'calls',10))
        self.addAsyncCleanup(client.close)
        return client, httpx.AsyncClient(transport=httpx.MockTransport(handler))

    async def test_thinking_state_and_budget_reach_payload(self):
        with patch.object(model_settings, 'settings_path', lambda: Path(self.tmp.name)/'settings.json'):
            spec = next(s for s in self.runtime.models.specs if s['provider_id']=='qwen')
            payloads=[]
            client, transport = self.mock_client(spec, payloads)
            await client._client.aclose()
            client._client = transport
            self.runtime.models.clients[spec['id']]=client
            with patch('core.providers.read_key',return_value='fake'):
                model_settings.update_settings(spec['id'],thinking=True,budget=4096)
                _=[e async for e in self.service.stream_chat(ChatRequest(message='hi',model=spec['id']))]
                self.assertTrue(payloads[-1]['enable_thinking'])
                self.assertEqual(payloads[-1]['thinking_budget'],4096)
                model_settings.update_settings(spec['id'],thinking=False)
                _=[e async for e in self.service.stream_chat(ChatRequest(message='hi',model=spec['id']))]
                self.assertFalse(payloads[-1]['enable_thinking'])
                self.assertNotIn('thinking_budget',payloads[-1])
                model_settings.update_settings(spec['id'],thinking=None)
                _=[e async for e in self.service.stream_chat(ChatRequest(message='hi',model=spec['id']))]
                self.assertFalse(payloads[-1]['enable_thinking'])

    async def test_discovered_default_sends_no_thinking_params(self):
        with patch.object(model_settings, 'settings_path', lambda: Path(self.tmp.name)/'settings.json'):
            spec={'id':'qwen:qwen3.8-flash','provider_id':'qwen','provider_name':'通义千问','model':'qwen3.8-flash','kind':'cloud','base_url':'https://example.invalid','api_key_env':'IN1_FAKE_KEY'}
            self.runtime.models.specs.append(spec)
            payloads=[]
            client, transport = self.mock_client(spec, payloads)
            await client._client.aclose()
            client._client = transport
            self.runtime.models.clients[spec['id']]=client
            with patch('core.providers.read_key',return_value='fake'):
                _=[e async for e in self.service.stream_chat(ChatRequest(message='hi',model='qwen3.8-flash'))]
                self.assertNotIn('enable_thinking',payloads[-1])
                self.assertNotIn('当前思考输出关闭',payloads[-1]['messages'][0]['content'])

    async def test_ollama_think_field_sent_only_when_set(self):
        payloads=[]
        def handler(request):
            payloads.append(json.loads(request.content))
            return httpx.Response(200,text=json.dumps({'message':{'content':'ok'},'done':True})+'\n')
        client=LLMClient('http://ollama.invalid','m')
        self.addAsyncCleanup(client.close)
        await client._client.aclose()
        client._client=httpx.AsyncClient(transport=httpx.MockTransport(handler))
        _=[e async for e in client.chat_stream_with_tools([{'role':'user','content':'hi'}])]
        self.assertNotIn('think',payloads[-1])
        client.think=True
        _=[e async for e in client.chat_stream_with_tools([{'role':'user','content':'hi'}])]
        self.assertTrue(payloads[-1]['think'])

class ShutdownStackTests(unittest.IsolatedAsyncioTestCase):
    async def test_shutdown_stack_endpoint_guards_and_payload(self):
        from bff import app as bff_app
        from core.stock_service import stock_service
        from unittest.mock import AsyncMock
        with patch.object(bff_app, '_stack_shutdown_targets', return_value={'bff': [1], 'web': [2], 'stock-bridge': [3]}), \
             patch.object(bff_app, 'schedule_stack_shutdown') as schedule, \
             patch.object(stock_service, 'status', AsyncMock(return_value={'online': True})), \
             patch.object(stock_service, 'request', AsyncMock(return_value={'status': 'shutting_down'})) as stop:
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=bff_app.app, client=('127.0.0.1', 1234)), base_url='http://test') as client:
                denied = await client.post('/api/admin/shutdown-stack', headers={'origin': 'https://untrusted.invalid'})
                self.assertEqual(denied.status_code, 403)
                schedule.assert_not_called()
                ok = await client.post('/api/admin/shutdown-stack')
                self.assertEqual(ok.status_code, 200)
                self.assertEqual(ok.json()['stopping'], {'bff': [1], 'web': [2]})
                schedule.assert_called_once_with({'bff': [1], 'web': [2]})
                stop.assert_awaited_once_with('/api/control/action', {'action': 'shutdown'})
