"""A new task namespace uses existing run routes without host business branches."""
import os
import unittest
from types import SimpleNamespace
from unittest.mock import patch
import httpx
from harness.runtime.tasks import TaskRegistry
from harness.contracts.tasks import TaskUnavailable
from plugins.finance.tasks import FinanceTasks


class DocumentTasks:
    namespace = 'documents'
    def __init__(self):
        self.row = {'id': 'documents:one', 'status': 'PAUSED', 'allowed_actions': ['stop']}
    async def list(self, workspace_id):
        return {'runs': [self.row], 'status': {'online': True}}
    async def get(self, identity):
        if identity != self.row['id']:
            raise KeyError('missing')
        return self.row
    async def control(self, identity, action):
        if action not in self.row['allowed_actions']:
            raise TaskUnavailable('operation not allowed by provider')
        self.row = {**self.row, 'status': 'STOPPED', 'allowed_actions': []}
        return self.row


class TaskProviderFlowTests(unittest.IsolatedAsyncioTestCase):
    async def test_new_namespace_lists_observes_and_controls_through_original_api(self):
        from bff.app import app
        registry = TaskRegistry(); registry.register(DocumentTasks())
        runtime = SimpleNamespace(task_providers=registry)
        service = SimpleNamespace(runs=SimpleNamespace(store=SimpleNamespace(list=lambda _: [])), run_summary=lambda x:x)
        with patch.dict(os.environ, {'DAILY_SERVICES': '1'}), \
                patch('core.ui_session.session_token', return_value='test-page-token'), \
                patch('bff.runs.get_runtime', return_value=runtime), \
                patch('bff.runs.get_chat_service', return_value=service):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://127.0.0.1:9510',
                    headers={'X-Harness-Session':'test-page-token'}) as client:
                listed = (await client.get('/api/runs')).json()
                self.assertEqual(listed['runs'][0]['id'], 'documents:one')
                self.assertTrue(listed['providers']['documents']['online'])
                events = await client.get('/api/runs/documents:one/events')
                self.assertIn('PAUSED', events.text)
                denied = await client.post('/api/runs/documents:one/control', json={'action':'stop'})
                self.assertEqual(denied.status_code, 403)
                rejected = await client.post('/api/runs/documents:one/control', json={'action':'resume'},
                                             headers={'Origin':'http://127.0.0.1:9510'})
                self.assertEqual(rejected.status_code, 409)
                stopped = await client.post('/api/runs/documents:one/control', json={'action':'stop'},
                                            headers={'Origin':'http://127.0.0.1:9510'})
                self.assertEqual(stopped.json()['status'], 'STOPPED')
                self.assertEqual((await client.get('/api/runs/absent:one')).status_code, 404)

    async def test_finance_adapter_keeps_owner_control_and_offline_semantics(self):
        from unittest.mock import AsyncMock
        service = SimpleNamespace(status=AsyncMock(return_value={'online':False}), request=AsyncMock())
        with patch('core.stock_service.stock_service', service):
            provider = FinanceTasks()
            with self.assertRaises(TaskUnavailable):
                await provider.control('stock:old', 'resume')
            service.request.assert_not_awaited()
            service.status.return_value = {'online':True}
            service.request.return_value = {'status':'STOPPED'}
            await provider.control('stock:a/b', 'stop')
            service.request.assert_awaited_once_with('/api/control/jobs/a%2Fb', {'action':'stop'}, timeout=40)
