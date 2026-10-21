"""A plugin receives frozen authority and stable requests through the public contract."""
import importlib
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock
import httpx
from harness.contracts.tool_provider import ExecutionContext
from core.workspaces import WorkspaceSnapshot

Transport = importlib.import_module('plugins.document-batch.transport').BatchTransport


class ContainerTransportTests(unittest.IsolatedAsyncioTestCase):
    async def test_prepared_but_unstarted_workspace_does_not_hide_running_workspace(self):
        tasks = importlib.import_module('plugins.document-batch.tasks').BatchTasks
        async def health(workspace):
            if workspace == 'default':
                raise httpx.ConnectError('not started')
        record = {'id': 'a' * 64, 'files': [], 'request': {'context': {'conversation_id': 'c'}},
                  'state': 'RUNNING', 'created_at': '2026-10-21T00:00:00Z'}
        client = SimpleNamespace(identity='document-batch:ocr', health=health,
            workspaces=SimpleNamespace(records=lambda: [{'id': 'default'}, {'id': '1' * 12}]),
            store=SimpleNamespace(list=lambda *_: {'document-batch:ocr': {}}),
            request=AsyncMock(return_value={'tasks': [record]}))
        result = await tasks(client).list(None)
        self.assertEqual(len(result['runs']), 1)
        self.assertEqual(result['runs'][0]['workspace_id'], '1' * 12)
        self.assertTrue(result['status']['online'])
        self.assertIn('default', result['status']['message'])

    async def test_host_context_idempotency_and_readonly_denial(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            snapshot = WorkspaceSnapshot({'id': 'default', 'version': 1, 'roots': []}, root)
            (root / 'scan.pdf').write_bytes(b'original')
            request = AsyncMock(return_value={'id': 'a' * 64, 'state': 'QUEUED'})
            client = SimpleNamespace(request=request, workspaces=SimpleNamespace(snapshot=lambda _: snapshot))
            factory = Transport(client)
            context = ExecutionContext('default', root, snapshot.resolve, 'conversation', 'run', 'chosen-model',
                                       frozenset({'workspace.read', 'workspace.write'}))
            bound = factory.bind(context)
            self.assertIsNone(factory.context)
            for _ in range(2):
                receipt = await bound.call_tool('documents_batch', {'paths': ['scan.pdf']}, 'conversation')
                self.assertEqual(receipt['id'], 'a' * 64)
                self.assertEqual(receipt['model_observation']['task_id'], 'a' * 16)
                self.assertNotIn('id', receipt['model_observation'])
            first, second = request.await_args_list
            self.assertEqual(first.args[3]['request_id'], second.args[3]['request_id'])
            self.assertEqual(first.args[3]['arguments']['paths'], ['/workspace/scan.pdf'])
            self.assertEqual(first.args[3]['context'], {'conversation_id': 'conversation', 'run_id': 'run', 'model': 'chosen-model'})
            with self.assertRaises(PermissionError):
                await bound.call_tool('documents_batch', {'paths': ['scan.pdf']}, 'foreign')
            readonly = factory.bind(ExecutionContext('default', root, snapshot.resolve, 'conversation', 'readrun', 'local', frozenset({'workspace.read'})))
            with self.assertRaises(PermissionError):
                await readonly.call_tool('documents_batch', {'paths': ['scan.pdf']}, 'conversation')
            self.assertEqual(request.await_count, 2)
