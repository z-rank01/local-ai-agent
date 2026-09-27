"""Conversation method choice is durable and does not affect older messages."""
import tempfile
import unittest
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
