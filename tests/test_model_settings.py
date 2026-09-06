"""Offline settings and live-stream contract checks, without real model calls."""
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import httpx
from core import config, model_settings
from core.providers import CompatibleClient, CallBudget
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
