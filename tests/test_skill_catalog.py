"""Persistent Skill activation, isolation, grants and rollback behavior."""
import sqlite3
from contextlib import closing
import tempfile
import unittest
from pathlib import Path
from core.conversation_store import ConversationStore
from harness.capabilities.skill_catalog import SkillCatalog
from harness.infrastructure.capability_store import CapabilityStore


def package(revision, **extra):
    return {'name': 'notes', 'revision': revision, 'path': '/versions/' + revision,
            'description': '会议整理', 'permissions': [], 'dependencies': [], **extra}


def snapshot(entry):
    return {'versions': {'notes': entry}, 'invalid': []}


class SkillCatalogFlowTests(unittest.TestCase):
    def test_update_disable_rollback_and_permission_changes_preserve_frozen_revision(self):
        with tempfile.TemporaryDirectory() as directory:
            store = ConversationStore(Path(directory) / 'conversations.db')
            persistence = CapabilityStore(store._connect)
            catalog = SkillCatalog(persistence, 'one', ['websearch'], ['workspace.read'])
            original = catalog.effective(snapshot(package('a')))
            self.assertEqual(catalog.effective(snapshot(package('b')))['versions']['notes']['revision'], 'b')
            self.assertEqual(original['versions']['notes']['revision'], 'a')
            catalog.action('notes', 'rollback', 'a')
            self.assertEqual(catalog.effective(snapshot(package('b')))['versions']['notes']['revision'], 'a')
            catalog.action('notes', 'disable')
            self.assertEqual(catalog.effective(snapshot(package('b')))['versions'], {})
            catalog.action('notes', 'enable')
            candidate = package('c', permissions=['workspace.read'], dependencies=['websearch'])
            catalog.reconcile(snapshot(candidate))
            self.assertEqual(persistence.list('one')['notes']['active']['revision'], 'a')
            catalog.action('notes', 'activate', 'c')
            self.assertEqual(catalog.effective(snapshot(candidate))['versions']['notes']['revision'], 'c')
            forbidden = package('d', permissions=['arbitrary-host-command'])
            catalog.reconcile(snapshot(forbidden))
            with self.assertRaisesRegex(ValueError, '未授权'):
                catalog.action('notes', 'activate', 'd')
            self.assertEqual(persistence.list('one')['notes']['active']['revision'], 'c')
            other = SkillCatalog(persistence, 'two')
            self.assertEqual(other.effective(snapshot(package('a')))['versions']['notes']['revision'], 'a')
            catalog.action('notes', 'uninstall')
            self.assertEqual(catalog.effective(snapshot(candidate))['versions'], {})
            self.assertTrue(persistence.list('two')['notes']['enabled'])
            reopened = CapabilityStore(ConversationStore(store._db_path)._connect)
            self.assertTrue(reopened.list('one')['notes']['uninstalled'])
            self.assertTrue(reopened.list('one')['notes']['history'])

    def test_incremental_upgrade_backs_up_without_losing_conversations(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'conversations.db'
            ConversationStore(path)
            with closing(sqlite3.connect(path)) as db, db:
                db.execute('DROP TABLE capability_installs')
                db.execute('PRAGMA user_version=9')
                db.execute("INSERT INTO conversations(id,title,model,created_at,updated_at) VALUES('retained','原会话','m','d','d')")
            store = ConversationStore(path)
            self.assertEqual(store.get_conversation('retained').title, '原会话')
            self.assertTrue(path.with_suffix('.before-v10.sqlite').exists())
            with closing(sqlite3.connect(path.with_suffix('.before-v10.sqlite'))) as db:
                self.assertEqual(db.execute('PRAGMA user_version').fetchone()[0], 9)
            state = CapabilityStore(store._connect)
            state.save('default', 'notes', {'enabled': False})
            self.assertFalse(state.list('default')['notes']['enabled'])
