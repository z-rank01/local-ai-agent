"""Conversation-level capability selection: persistence, validation and run focus."""
import copy
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from fastapi import HTTPException

from bff.schemas import ChatRequest
from core.conversation_store import ConversationStore
from harness.capabilities.selection import filter_capability_snapshot
from harness.contracts.capabilities import normalize_capability_ids
from tests import test_in1


class CapabilitySelectionStoreTests(unittest.TestCase):
    def test_selection_persists_per_conversation_without_reordering(self):
        with tempfile.TemporaryDirectory() as folder:
            store = ConversationStore(Path(folder) / 'conversations.db')
            first = store.create_conversation(title='A', model='m')
            second = store.create_conversation(title='B', model='m')
            before = store.get_conversation(first.id).updated_at
            store.set_capability_ids(first.id, ['skill:meeting-notes', 'plugin:websearch'])
            reopened = ConversationStore(Path(folder) / 'conversations.db')
            self.assertEqual(reopened.get_conversation(first.id).capability_ids,
                             ['skill:meeting-notes', 'plugin:websearch'])
            self.assertEqual(reopened.get_conversation(first.id).updated_at, before)
            self.assertEqual(reopened.get_conversation(second.id).capability_ids, [])
            self.assertEqual({row.id: row.capability_ids for row in reopened.list_conversations()}[first.id],
                             ['skill:meeting-notes', 'plugin:websearch'])
            with self.assertRaises(ValueError):
                store.set_capability_ids('missing', [])

    def test_read_deduplicates_and_tolerates_corrupt_rows(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'conversations.db'
            store = ConversationStore(path)
            conv = store.create_conversation(title='A', model='m')
            with closing(sqlite3.connect(path)) as db, db:
                db.execute("UPDATE conversations SET capability_ids=? WHERE id=?",
                           ('["skill:a","skill:a","plugin:b"]', conv.id))
            self.assertEqual(store.get_conversation(conv.id).capability_ids, ['skill:a', 'plugin:b'])
            with closing(sqlite3.connect(path)) as db, db:
                db.execute("UPDATE conversations SET capability_ids='not-json' WHERE id=?", (conv.id,))
            self.assertEqual(store.get_conversation(conv.id).capability_ids, [])

    def test_v10_upgrade_backs_up_and_preserves_conversations(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'conversations.db'
            store = ConversationStore(path)
            conv = store.create_conversation(title='旧会话', model='m')
            with closing(sqlite3.connect(path)) as db, db:
                db.execute('ALTER TABLE conversations DROP COLUMN capability_ids')
                db.execute('PRAGMA user_version=10')
            upgraded = ConversationStore(path)
            self.assertEqual(upgraded.get_conversation(conv.id).title, '旧会话')
            self.assertEqual(upgraded.get_conversation(conv.id).capability_ids, [])
            backup = path.with_suffix('.before-v11.sqlite')
            self.assertTrue(backup.exists())
            with closing(sqlite3.connect(backup)) as db:
                self.assertEqual(db.execute('PRAGMA user_version').fetchone()[0], 10)
                columns = {row[1] for row in db.execute('PRAGMA table_info(conversations)')}
                self.assertNotIn('capability_ids', columns)


class CapabilitySelectionValidationTests(unittest.TestCase):
    def test_normalizes_order_and_deduplicates(self):
        self.assertEqual(normalize_capability_ids(None), [])
        self.assertEqual(normalize_capability_ids([]), [])
        self.assertEqual(normalize_capability_ids(
            [' skill:meeting-notes ', 'plugin:websearch', 'skill:meeting-notes']),
            ['skill:meeting-notes', 'plugin:websearch'])

    def test_rejects_malformed_and_oversized_selections(self):
        for bad in ([''], ['unknown:a'], [':a'], ['skill:'], [1], 'skill:a', ['skill:' + 'x' * 120]):
            with self.subTest(value=bad), self.assertRaises(ValueError):
                normalize_capability_ids(bad)
        with self.assertRaises(ValueError):
            normalize_capability_ids([f'skill:s{index}' for index in range(65)])


class CapabilitySelectionFilterTests(unittest.TestCase):
    def snapshot(self):
        return {
            'versions': {'meeting': {'name': 'meeting'}, 'notes': {'name': 'notes'}},
            'capabilities': [{'id': 'websearch', 'tools': ['web_search']},
                             {'id': 'finance', 'tools': ['stock_report_read']}],
            'invalid': [{'name': 'broken'}],
        }

    def test_empty_selection_keeps_the_same_catalog(self):
        snapshot = self.snapshot()
        self.assertIs(filter_capability_snapshot(snapshot, []), snapshot)
        self.assertIs(filter_capability_snapshot(snapshot, None), snapshot)

    def test_filters_skills_and_plugins_without_mutating_input(self):
        snapshot = self.snapshot()
        before = copy.deepcopy(snapshot)
        filtered = filter_capability_snapshot(
            snapshot, ['skill:meeting', 'plugin:websearch', 'skill:unknown'])
        self.assertEqual(list(filtered['versions']), ['meeting'])
        self.assertEqual([row['id'] for row in filtered['capabilities']], ['websearch'])
        self.assertEqual(filtered['invalid'], snapshot['invalid'])
        self.assertEqual(snapshot, before)

    def test_unknown_selection_yields_an_empty_catalog(self):
        filtered = filter_capability_snapshot(self.snapshot(), ['skill:missing', 'plugin:missing'])
        self.assertEqual(filtered['versions'], {})
        self.assertEqual(filtered['capabilities'], [])


class CapabilitySelectionServiceTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = test_in1.ServiceTests.asyncSetUp

    async def test_patch_persists_and_chat_admission_updates_selection(self):
        conv = self.service.create_conversation('A', 'qwen:qwen3.5-flash')
        summary = self.service.update_conversation(
            conv.id, capability_ids=['skill:meeting-notes', 'plugin:websearch', 'skill:meeting-notes'])
        self.assertEqual(summary.capability_ids, ['skill:meeting-notes', 'plugin:websearch'])
        events = [event async for event in self.service.stream_chat(ChatRequest(
            conversation_id=conv.id, message='hello', model='qwen:qwen3.5-flash',
            capability_ids=['plugin:websearch']))]
        self.assertEqual(events[0].event, 'session.started')
        self.assertEqual(events[0].data['conversation']['capability_ids'], ['plugin:websearch'])
        self.assertEqual(self.service.get_conversation(conv.id).capability_ids, ['plugin:websearch'])

    async def test_new_conversation_keeps_request_selection(self):
        events = [event async for event in self.service.stream_chat(ChatRequest(
            message='hello', model='qwen:qwen3.5-flash',
            capability_ids=['skill:meeting-notes']))]
        conversation_id = events[0].conversation_id
        self.assertEqual(self.service.get_conversation(conversation_id).capability_ids,
                         ['skill:meeting-notes'])

    async def test_invalid_or_empty_selection_is_validated(self):
        conv = self.service.create_conversation('A', 'qwen:qwen3.5-flash')
        with self.assertRaises(HTTPException) as raised:
            self.service.update_conversation(conv.id, capability_ids=['bad'])
        self.assertEqual(raised.exception.status_code, 422)
        with self.assertRaises(HTTPException) as raised:
            self.service.update_conversation(conv.id)
        self.assertEqual(raised.exception.status_code, 422)
        self.service.update_conversation(conv.id, capability_ids=['plugin:websearch'])
        self.service.update_conversation(conv.id, capability_ids=[])
        self.assertEqual(self.service.get_conversation(conv.id).capability_ids, [])
