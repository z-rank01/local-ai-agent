"""Local file delivery is persistent and never a model turn."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi import HTTPException
from fastapi.testclient import TestClient

from core.conversation_store import ConversationStore
from core.workspaces import WorkspaceManager
from bff.service import ChatSessionService
from bff.app import app


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
        self.service._active_conversations = set()
        self.service.workspaces = WorkspaceManager(self.store, self.root)
        self.file = self.root / 'report.html'
        self.file.write_text('<html><title>人工简报</title><p>已保存正文</p></html>', encoding='utf-8')

    def test_persisted_delivery_is_idempotent_and_explicitly_not_model_generated(self):
        first = self.service.deliver_workspace_brief(self.conv.id, '/workspace/report.html')
        again = self.service.deliver_workspace_brief(self.conv.id, '/workspace/report.html')
        self.assertEqual(first.id, again.id)
        self.assertIn('非模型生成', first.model)
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
