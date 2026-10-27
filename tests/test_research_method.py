"""Conversation method choice is durable and does not affect older messages."""
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx
from pathlib import Path

from core.conversation_store import ConversationStore


class ResearchMethodStoreTests(unittest.TestCase):
    def test_choice_persists_per_conversation(self):
        with tempfile.TemporaryDirectory() as folder:
            store = ConversationStore(Path(folder) / 'conversations.db')
            first = store.create_conversation()
            second = store.create_conversation()
            store.set_research_method(first.id, 'kimi-chain')
            reopened = ConversationStore(Path(folder) / 'conversations.db')
            self.assertEqual(reopened.get_conversation(first.id).research_method, 'kimi-chain')
            self.assertEqual(reopened.get_conversation(second.id).research_method, 'auto')
            self.assertEqual({row.id: row.research_method for row in reopened.list_conversations()}[first.id],
                             'kimi-chain')


class ResearchMethodAPITests(unittest.IsolatedAsyncioTestCase):
    async def test_dynamic_methods_selection_and_invalid_id(self):
        from bff import app as bff_app
        from bff.service import ChatSessionService
        from core import config
        from plugins.finance.service import stock_service
        with tempfile.TemporaryDirectory() as folder:
            base=Path(folder)
            store=ConversationStore(base/'conversations.db')
            with patch.object(config,'WORKSPACE_PATH',base/'workspace'), patch.object(config,'PROJECT_ROOT',base):
                service=ChatSessionService(SimpleNamespace(store=store,models=SimpleNamespace(default='test'),
                    capability_services={'finance': stock_service}))
                first=store.create_conversation();other=store.create_conversation()
                rows=[{'id':mid,'title':title,'roles':roles,'source':'项目适配','source_version':'1',
                       'version':'hash','content':'private method body','provenance':['local file']}
                      for mid,title,roles in [('earnings-review','业绩解读（试用）',['C03']),
                          ('policy-transmission','政策传导（试用）',['C01','C02']),
                          ('event-review','事件解读（试用）',['market_brief'])]]
                with patch.object(bff_app,'get_chat_service',return_value=service), \
                     patch.object(stock_service,'request',AsyncMock(return_value={'methods':rows})):
                    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=bff_app.app,
                        client=('127.0.0.1',1234)),base_url='http://test') as client:
                        response=await client.get('/api/research-methods')
                        self.assertEqual(response.status_code,200)
                        returned=response.json()['methods']
                        self.assertEqual([row['id'] for row in returned],[row['id'] for row in rows])
                        self.assertNotIn('content',returned[0]);self.assertNotIn('provenance',returned[0])
                        for row in rows:
                            response=await client.put(f'/api/conversations/{first.id}/research-method',
                                                      json={'method_id':row['id']})
                            self.assertEqual(response.status_code,200)
                            self.assertEqual(response.json()['research_method'],row['id'])
                        bad=await client.put(f'/api/conversations/{first.id}/research-method',
                                             json={'method_id':'invalid'})
                        self.assertEqual(bad.status_code,422)
                        self.assertEqual(store.get_conversation(first.id).research_method,'event-review')
                        self.assertEqual(store.get_conversation(other.id).research_method,'auto')
                        reset=await client.put(f'/api/conversations/{first.id}/research-method',
                                               json={'method_id':'auto'})
                        self.assertEqual(reset.status_code,200)
