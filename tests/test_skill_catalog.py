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

    def test_legacy_environment_change_waits_for_review_and_checks_current_environment(self):
        with tempfile.TemporaryDirectory() as directory:
            persistence = CapabilityStore(ConversationStore(Path(directory) / 'conversations.db')._connect)
            catalog = SkillCatalog(persistence, 'one')
            first = package('a', kind='python', environment_dependencies=[])
            catalog.effective(snapshot(first))
            update = package('b', kind='python', environment_dependencies=['example>=2'],
                             environment_errors=['依赖缺失'])
            catalog.reconcile(snapshot(update))
            with self.assertRaisesRegex(ValueError, '依赖缺失'):
                catalog.action('notes', 'activate', 'b')
            self.assertEqual(persistence.list('one')['notes']['active']['revision'], 'a')
            catalog.action('notes', 'activate', 'b', environment_errors=[])
            unavailable = {**update, 'environment_errors': ['依赖已被移除']}
            self.assertEqual(catalog.effective(snapshot(unavailable))['versions'], {})
            with self.assertRaisesRegex(ValueError, '当前环境'):
                catalog.action('notes', 'rollback', 'a', environment_errors=['当前环境不可用'])

    def test_invalid_update_retains_last_valid_revision_but_removal_stops_discovery(self):
        with tempfile.TemporaryDirectory() as directory:
            persistence = CapabilityStore(ConversationStore(Path(directory) / 'conversations.db')._connect)
            catalog = SkillCatalog(persistence, 'one')
            catalog.effective(snapshot(package('valid')))
            broken = {'versions': {}, 'invalid': [{'name': 'notes', 'error': '源文件语法无效'}]}
            kept = catalog.effective(broken)
            self.assertEqual(kept['versions']['notes']['revision'], 'valid')
            self.assertEqual(persistence.list('one')['notes']['candidate_error'], '源文件语法无效')
            self.assertEqual(catalog.effective({'versions': {}, 'invalid': []})['versions'], {})
            fixed = catalog.effective(snapshot(package('fixed')))
            self.assertEqual(fixed['versions']['notes']['revision'], 'fixed')
            self.assertNotIn('candidate_error', persistence.list('one')['notes'])

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

    def test_required_capabilities_block_but_optional_capabilities_can_degrade(self):
        with tempfile.TemporaryDirectory() as directory:
            persistence = CapabilityStore(ConversationStore(Path(directory) / 'conversations.db')._connect)
            catalog = SkillCatalog(persistence, 'one', available=['websearch'])
            required = package('required', required=['document-index'], optional=['websearch'])
            self.assertIn('document-index', catalog.blockers(required)[0])
            catalog.reconcile(snapshot(required))
            with self.assertRaisesRegex(ValueError, '缺少依赖'):
                catalog.action('notes', 'activate', 'required')
            optional = package('optional', optional=['document-index', 'websearch'])
            catalog.reconcile(snapshot(optional))
            catalog.action('notes', 'activate', 'optional')
            self.assertEqual(catalog.effective(snapshot(optional))['versions']['notes']['revision'], 'optional')


class SkillDiffApiTests(unittest.IsolatedAsyncioTestCase):
    async def test_before_is_validated_and_forwarded(self):
        from types import SimpleNamespace
        from unittest.mock import AsyncMock, patch
        import json
        import httpx
        from bff.skill_management import skill_diff
        with tempfile.TemporaryDirectory() as directory:
            store = ConversationStore(Path(directory) / 'conversations.db')
            CapabilityStore(store._connect).save('default', 'notes', {
                'enabled': True, 'active': {'revision': 'new'}, 'history': [{'revision': 'old'}]})
            record = {'id': 'default', 'runner_port': 12345}
            ensure = AsyncMock(return_value=record)
            service = SimpleNamespace(for_workspace=lambda workspace_id: SimpleNamespace(
                workspaces=SimpleNamespace(get=lambda name: record, ensure=ensure)))
            runtime = SimpleNamespace(store=store)
            payloads = []
            def handler(request):
                payloads.append(json.loads(request.content))
                return httpx.Response(200, json={'files': [{'change': 'modified'}]})
            real_client = httpx.AsyncClient
            with patch('bff.skill_management.get_chat_service', return_value=service), \
                    patch('bff.skill_management.get_runtime', return_value=runtime), \
                    patch('bff.skill_management.httpx.AsyncClient',
                          lambda **kwargs: real_client(transport=httpx.MockTransport(handler))):
                result = await skill_diff('default', 'notes', 'new', 'old')
            self.assertEqual(result['files'][0]['change'], 'modified')
            self.assertEqual(payloads, [{'name': 'notes', 'before': 'old', 'after': 'new'}])
            ensure.assert_awaited_once_with('default')
