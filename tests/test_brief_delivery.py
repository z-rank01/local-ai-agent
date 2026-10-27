"""Local file delivery is persistent and never a model turn."""
import tempfile
import unittest
from types import SimpleNamespace
from pathlib import Path
from unittest.mock import patch

from fastapi import HTTPException
from fastapi.testclient import TestClient

from core.conversation_store import ConversationStore
from core.workspaces import WorkspaceManager
from bff.service import ChatSessionService
from bff.app import app
from plugins.finance.service import stock_service


class BriefDeliveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        base = Path(self.temp.name)
        self.root = base / 'workspace'
        self.root.mkdir()
        self.store = ConversationStore(base / 'test.sqlite')
        self.conv = self.store.create_conversation(title='Existing research')
        self.service = object.__new__(ChatSessionService)
        self.service._store = self.store
        self.service._runtime = SimpleNamespace(artifact_providers=None,
            router=SimpleNamespace(provider_for=lambda owner: None),
            capability_services={'finance': stock_service})
        self.service._active_conversations = set()
        self.service.workspaces = WorkspaceManager(self.store, self.root)
        self.service._artifact_registry()
        self.file = self.root / 'report.html'
        self.file.write_text('<html><title>人工简报</title><p>已保存正文</p></html>', encoding='utf-8')

    def test_persisted_delivery_is_idempotent_and_explicitly_not_model_generated(self):
        first = self.service.deliver_workspace_brief(self.conv.id, '/workspace/report.html')
        again = self.service.deliver_workspace_brief(self.conv.id, '/workspace/report.html')
        self.assertEqual(first.id, again.id)
        self.assertIn('非模型生成', first.model)
        self.assertTrue(first.file_delivery)
        self.assertIn('[阅读简报](</workspace/report.html>)', first.content)
        self.assertEqual(self.service.get_messages(self.conv.id)[0].id, first.id)
        self.assertFalse(self.service.workspaces.active)
        self.assertEqual(len(self.store.get_messages(self.conv.id)), 1)

    def test_file_validation_failures_never_append_messages_or_leave_a_lease(self):
        for path in ('/workspace/../report.html', '/workspace/report.txt',
                     '/workspace/missing.html', '/roots/foreign/report.html',
                     '/workspace/report.html?x=1'):
            with self.subTest(path=path), self.assertRaises(HTTPException):
                self.service.deliver_workspace_brief(self.conv.id, path)
        self.file.write_text('x' * 1_000_001, encoding='utf-8')
        with self.assertRaises(HTTPException):
            self.service.deliver_workspace_brief(self.conv.id, '/workspace/report.html')
        self.assertEqual(self.store.get_messages(self.conv.id), [])
        self.assertFalse(self.service.workspaces.active)

    def test_running_or_overlapping_workspace_rejects_publication(self):
        self.service._active_conversations.add(self.conv.id)
        with self.assertRaises(HTTPException):
            self.service.deliver_workspace_brief(self.conv.id, '/workspace/report.html')
        self.service._active_conversations.clear()
        self.service.workspaces.acquire(self.service.workspaces.snapshot(), 'other')
        with self.assertRaises(HTTPException) as caught:
            self.service.deliver_workspace_brief(self.conv.id, '/workspace/report.html')
        self.assertEqual(caught.exception.status_code, 409)
        self.assertEqual(self.store.get_messages(self.conv.id), [])

    def test_http_requires_local_origin_and_does_not_accept_workspace_override(self):
        with patch('bff.app.get_chat_service', return_value=self.service):
            client = TestClient(app, client=('127.0.0.1', 1234))
            url = f'/api/conversations/{self.conv.id}/brief-delivery'
            body = {'path': '/workspace/report.html'}
            self.assertEqual(client.post(url, json=body).status_code, 403)
            headers = {'origin': 'http://127.0.0.1:9510'}
            self.assertEqual(client.post(url, json={**body, 'workspace_id': 'other'}, headers=headers).status_code, 422)
            self.assertEqual(client.post(url, json=body, headers=headers).status_code, 200)
            client.close()

    def test_research_archive_uses_conversation_workspace_not_active_scope(self):
        record = {**self.service.workspaces.get(), 'id': 'aabbccddeeff', 'name': '另一工作区'}
        self.service.workspaces.save(record)
        snapshot = self.service.workspaces.snapshot(record['id'])
        snapshot.managed.mkdir(parents=True)
        conversation = self.store.create_conversation(workspace_id=record['id'])
        row = self.store.add_message(conversation.id, role='tool', tool_name='stock_report_read',
                                     tool_result={'local_result': '第1页原研究材料'})
        before = vars(self.store.get_message(row.id)).copy()
        path = self.service.archive_artifact(conversation.id, row.id, '第1页原研究材料')
        self.assertEqual(snapshot.resolve(path).read_text(encoding='utf-8'), '第1页原研究材料')
        self.assertFalse((self.root / 'reports').exists())
        self.assertEqual(self.service.archive_artifact(conversation.id, row.id, '第1页原研究材料'), path)
        self.assertEqual(vars(self.store.get_message(row.id)), before)
        with self.assertRaises(HTTPException):
            self.service.archive_artifact(self.conv.id, row.id, 'other')

    def test_archive_io_failure_keeps_original_database_material(self):
        row = self.store.add_message(self.conv.id, role='tool', tool_name='stock_report_read',
                                     tool_result={'local_result': '原资料'})
        with patch('pathlib.Path.open', side_effect=PermissionError('denied')):
            with self.assertRaises(PermissionError):
                self.service.archive_artifact(self.conv.id, row.id, '原资料')
        self.assertIn('原资料', self.store.get_message(row.id).tool_result)
