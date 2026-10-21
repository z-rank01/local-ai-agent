import asyncio
import json
import os
import tempfile
import unittest
from contextlib import aclosing
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException

from core import config, model_settings
from core.conversation_store import ConversationStore
from core.workspaces import WorkspaceManager, WorkspaceSnapshot
from bff.schemas import ChatRequest, UIStreamEvent
from bff.service import ChatSessionService, exclusive_turn


class WorkspaceTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.managed = self.base / 'managed'
        self.managed.mkdir()
        self.a, self.b = self.base / '资料 A', self.base / '资料 B'
        self.a.mkdir(); self.b.mkdir()
        for key, value in [('WORKSPACE_PATH', self.managed), ('PROJECT_ROOT', self.base)]:
            p = patch.object(config, key, value); p.start(); self.addCleanup(p.stop)
        self.store = ConversationStore(self.base / 'conversations.db')
        self.service = ChatSessionService(SimpleNamespace(store=self.store, models=SimpleNamespace(default='test', resolve=lambda *args, **kwargs: ({'id': 'test', 'kind': 'local', 'model': 'test', 'provider_id': 'test'}, SimpleNamespace()))))
        self.manager = self.service.workspaces

    def workspace(self, name='资料', roots=None):
        record = self.manager.prepare({'name': name, 'roots': [{'host_path': str(p)} for p in (roots or [self.a, self.b])]})
        self.manager.save(record)
        self.manager.manifest()
        return record

    def test_old_conversations_default_and_many_chats_in_workspace(self):
        old = self.store.create_conversation(title='旧会话')
        workspace = self.workspace()
        first = self.service.create_conversation('财报', workspace_id=workspace['id'])
        second = self.service.create_conversation('对比', workspace_id=workspace['id'])
        self.assertEqual(self.store.get_conversation(old.id).workspace_id, 'default')
        self.assertEqual([c.id for c in self.store.list_conversations(workspace_id=workspace['id'])], [second.id, first.id])
        self.assertEqual(first.workspace_id, second.workspace_id)
        with self.assertRaises(HTTPException): self.manager.delete(workspace['id'])

    def test_body_search_with_workspace_filter(self):
        w = self.workspace()
        conv = self.store.create_conversation(title='财报', workspace_id=w['id'])
        self.store.add_message(conv.id, role='user', content='这里正文含现金流证据')
        self.assertEqual(len(self.service.list_conversations(query='现金流', workspace_id=w['id'])), 1)
        self.assertIn('现金流', self.service.list_conversations(query='现金流')[0].search_snippet)
        self.assertEqual(self.service.list_conversations(query='现金流', workspace_id='default'), [])

    def test_direct_host_mapping_and_traversal(self):
        w = self.workspace()
        snapshot = self.manager.snapshot(w['id'])
        source = self.a / '测试.txt'; source.write_text('内容', encoding='utf-8')
        virtual = snapshot.virtual(source)
        self.assertTrue(virtual.startswith('/roots/'))
        self.assertEqual(snapshot.resolve(virtual), source)
        with self.assertRaises(HTTPException): snapshot.resolve('/etc/passwd')
        with self.assertRaises(HTTPException): snapshot.resolve('/roots/' + w['roots'][0]['id'] + '/../../outside')
        scoped = self.service.for_workspace(w['id'])
        text = scoped.import_local_paths(str(source)).rewritten_text
        self.assertIn(virtual, text)
        self.assertFalse((scoped._workspace_root / 'data' / source.name).exists())

    def test_snapshot_survives_rename_and_configuration_change(self):
        old = self.workspace()
        frozen = self.manager.snapshot(old['id'])
        new = self.manager.prepare({'name': '新名称', 'roots': old['roots'][:1]}, old)
        self.manager.save(new)
        self.assertEqual(len(frozen.roots), 3)
        self.assertEqual(len(self.manager.snapshot(old['id']).roots), 2)
        self.assertEqual(new['roots'][0]['id'], old['roots'][0]['id'])

    def test_removed_root_identity_can_be_restored(self):
        old = self.workspace(roots=[self.a])
        removed = self.manager.prepare({'roots': []}, old)
        restored = self.manager.prepare({'roots': [{'host_path': str(self.a)}]}, removed)
        self.assertEqual(restored['roots'][0]['id'], old['roots'][0]['id'])
        self.assertNotIn('root_history', self.manager.public(restored))

    def test_primary_can_be_managed_and_invalid_root_is_rejected(self):
        old = self.workspace()
        managed = self.manager.prepare({'primary_root_id': None}, old)
        self.assertEqual(WorkspaceSnapshot(managed, self.managed).cwd, '/workspace')
        with self.assertRaises(HTTPException): self.manager.prepare({'primary_root_id': 'unknown'}, old)

    def test_duplicate_nested_and_managed_roots_rejected(self):
        child = self.a / 'nested'; child.mkdir()
        for paths in ([self.a, self.a], [self.a, child], [self.managed], [self.base]):
            with self.subTest(paths=paths), self.assertRaises(HTTPException): self.workspace(roots=paths)

    def test_invalid_name_missing_root_and_relative_root_rejected(self):
        for payload in ({'name': ''}, {'name': 'x', 'roots': [{'host_path': 'relative'}]},
                        {'name': 'x', 'roots': [{'host_path': str(self.base / 'missing')}]}):
            with self.subTest(payload=payload), self.assertRaises(HTTPException): self.manager.prepare(payload)

    def test_cloud_expansion_requires_confirmation_and_write_requires_read(self):
        old = self.workspace(roots=[self.a])
        old.update(cloud_read=True, cloud_write=True)
        with self.assertRaises(HTTPException): self.manager.prepare({'roots': old['roots'] + [{'host_path': str(self.b)}]}, old)
        new = self.manager.prepare({'roots': old['roots'] + [{'host_path': str(self.b)}], 'confirm_cloud_expansion': True}, old)
        self.assertTrue(new['cloud_write'])
        revoked = self.manager.prepare({'cloud_read': False}, new)
        self.assertFalse(revoked['cloud_write'])
        with self.assertRaises(HTTPException): self.manager.prepare({'cloud_read': False, 'cloud_write': True}, old)

    def test_overlap_lock_and_unrelated_parallelism(self):
        w = self.workspace(roots=[self.a]); x = self.workspace('重叠', [self.a]); y = self.workspace('独立', [self.b])
        lease = self.manager.acquire(self.manager.snapshot(w['id']), 'running')
        with self.assertRaises(HTTPException) as raised: self.manager.acquire(self.manager.snapshot(x['id']), 'other')
        self.assertEqual(raised.exception.status_code, 409)
        self.assertEqual(raised.exception.detail['conversation_id'], 'running')
        lease2 = self.manager.acquire(self.manager.snapshot(y['id']), 'parallel')
        self.manager.active.pop(lease); self.manager.active.pop(lease2)
        self.manager.acquire(self.manager.snapshot(x['id']), 'after')

    def test_managed_subdirectories_do_not_lock_default_workspace(self):
        w = self.workspace(roots=[self.a])
        self.manager.acquire(self.manager.snapshot(w['id']), 'running')
        self.assertIsNone(self.manager.conflict(self.manager.snapshot('default')))
        with self.assertRaises(HTTPException): self.manager.snapshot('default').resolve('/workspace/.harness/' + w['id'])

    async def test_failure_retains_active_config_and_pending_can_activate(self):
        old = self.workspace(roots=[self.a])
        with patch.object(self.manager, 'reconcile', side_effect=RuntimeError('控制器不可用')):
            result = await self.manager.update({'roots': old['roots'] + [{'host_path': str(self.b)}]}, old['id'])
        self.assertTrue(result['pending'])
        self.assertEqual(len(self.manager.snapshot(old['id']).roots), 2)
        with patch('core.service_supervisor.run_operation', new=AsyncMock()), patch.object(self.manager, 'verify', new=AsyncMock()):
            await self.manager.reconcile(old['id'])
        self.assertEqual(len(self.manager.snapshot(old['id']).roots), 3)
        self.assertFalse(self.manager.public(self.manager.get(old['id']))['pending'])

    async def test_running_task_blocks_configuration_update(self):
        old = self.workspace()
        self.manager.acquire(self.manager.snapshot(old['id']), 'running')
        with self.assertRaises(HTTPException): await self.manager.update({'name': '改名'}, old['id'])
        self.assertEqual(self.manager.get(old['id'])['name'], old['name'])

    async def test_turn_locks_survive_observer_disconnect_until_explicit_stop(self):
        import asyncio
        hold=asyncio.Event()
        w = self.workspace()
        conv = self.store.create_conversation(workspace_id=w['id'])
        @exclusive_turn
        async def probe(service, request):
            yield UIStreamEvent(event='session.started', conversation_id=request.conversation_id)
            await hold.wait()
            yield UIStreamEvent(event='session.completed', conversation_id=request.conversation_id)
        with patch.object(self.manager, 'ensure', new=AsyncMock(return_value=self.manager.snapshot(w['id']))):
            async with aclosing(probe(self.service, ChatRequest(message='a', conversation_id=conv.id))) as first:
                await anext(first)
                other = self.store.create_conversation(workspace_id=w['id'])
                with self.assertRaises(HTTPException): await anext(probe(self.service, ChatRequest(message='b', conversation_id=other.id)))
            self.assertTrue(self.manager.active)
            run=next(iter(self.service.runs.live))
            await self.service.runs.action(run,'stop')
            self.assertEqual(self.manager.active, {})
            self.assertEqual(self.service._active_conversations, set())

    async def test_mismatched_conversation_workspace_rejected_before_mutation(self):
        w = self.workspace(); conv = self.store.create_conversation()
        with self.assertRaises(HTTPException):
            await anext(self.service.stream_chat(ChatRequest(message='x', conversation_id=conv.id, workspace_id=w['id'])))
        self.assertEqual(self.store.get_messages(conv.id), [])

    def test_directory_disappearance_does_not_fallback(self):
        w = self.workspace(roots=[self.a]); snap = self.manager.snapshot(w['id'])
        self.a.rmdir()
        with self.assertRaises(HTTPException): snap.resolve('/roots/' + w['roots'][0]['id'] + '/file.txt')
        self.assertFalse(self.manager.public(w)['roots'][0]['available'])

    def test_deleting_unused_workspace_keeps_original_files(self):
        w = self.workspace(roots=[self.a]); source = self.a / 'keep.txt'; source.write_text('keep')
        self.manager.delete(w['id'])
        self.assertTrue(source.exists())

    async def test_restricted_host_upload_uses_container_and_cleans_staging(self):
        w = self.workspace(roots=[self.a])
        scoped = self.service.for_workspace(w['id'])
        async def chunks():
            yield b'\x00binary\xff'
        async def proxy(method, path, *, content):
            payload = b''.join([chunk async for chunk in content])
            (self.a / 'image.bin').write_bytes(payload)
            return {'size': len(payload)}
        with patch('bff.service.shutil.copyfile', side_effect=PermissionError('restricted process')), patch.object(scoped, '_call_skill_files', side_effect=proxy) as delegated:
            result = await scoped.upload_workspace_file(filename='image.bin', content_type='application/octet-stream', target_dir=scoped._snapshot.cwd, chunks=chunks())
        self.assertEqual((self.a / 'image.bin').read_bytes(), b'\x00binary\xff')
        self.assertTrue(result.attachment.workspace_path.startswith('/roots/'))
        self.assertEqual(list((scoped._workspace_root / '.uploads').iterdir()), [])
        self.assertEqual(delegated.call_count, 1)

    async def test_interrupted_upload_does_not_publish_partial_file(self):
        w = self.workspace(roots=[self.a]); scoped = self.service.for_workspace(w['id'])
        async def chunks():
            yield b'partial'
            raise RuntimeError('disconnected')
        with self.assertRaises(RuntimeError):
            await scoped.upload_workspace_file(filename='partial.bin', content_type=None, target_dir=scoped._snapshot.cwd, chunks=chunks())
        self.assertFalse((self.a / 'partial.bin').exists())
        self.assertEqual(list((scoped._workspace_root / '.uploads').iterdir()), [])

    def test_running_conversation_cannot_be_deleted(self):
        conv = self.store.create_conversation()
        message = self.store.add_message(conv.id, role='user', content='keep')
        self.service._active_conversations.add(conv.id)
        with self.assertRaises(HTTPException): self.service.delete_conversation(conv.id)
        with self.assertRaises(HTTPException): self.service.delete_message(conv.id, message.id)
        with self.assertRaises(HTTPException): self.service.activate_message_version(conv.id, message_id=message.id, version_number=1)
        self.assertIsNotNone(self.store.get_conversation(conv.id))

    def test_symlink_escape(self):
        w = self.workspace(roots=[self.a]); link = self.a / 'link'
        try: link.symlink_to(self.b, target_is_directory=True)
        except OSError: self.skipTest('symbolic links unavailable')
        with self.assertRaises(HTTPException): self.manager.snapshot(w['id']).resolve('/roots/' + w['roots'][0]['id'] + '/link/file')

    def test_tool_credentials_are_private_stable_and_workspace_scoped(self):
        from core.workspaces import tool_token
        w = self.workspace()
        token = tool_token(w['id'])
        self.assertGreaterEqual(len(token), 32)
        self.assertEqual(tool_token(w['id']), token)
        self.assertNotEqual(tool_token('default'), token)
        self.assertNotIn(token, json.dumps(self.manager.public(w)))
        with self.assertRaises(ValueError): tool_token('../escape')
        with self.assertRaises(HTTPException):
            self.manager.prepare({'name': 'private', 'roots': [{'host_path': str(self.base / 'data/private')}]})

    async def test_unrelated_workspace_can_execute_during_configuration_change(self):
        first = self.workspace(roots=[self.a])
        second = self.workspace(roots=[self.b])
        entered, release = asyncio.Event(), asyncio.Event()
        async def reconcile(workspace_id):
            entered.set()
            await release.wait()
        with patch.object(self.manager, 'reconcile', side_effect=reconcile):
            pending = asyncio.create_task(self.manager.update({'name': '修改中'}, first['id']))
            await entered.wait()
            lease = self.manager.acquire(self.manager.snapshot(second['id']), 'independent')
            with self.assertRaises(HTTPException): self.manager.acquire(self.manager.snapshot(first['id']), 'blocked')
            self.manager.active.pop(lease)
            release.set()
            await pending

    def test_browser_credential_header_and_cookie(self):
        from core.ui_session import authenticated, session_token
        from starlette.requests import Request
        token = session_token()
        def request(headers):
            return Request({'type': 'http', 'headers': headers})
        self.assertFalse(authenticated(request([])))
        self.assertFalse(authenticated(request([(b'x-harness-session', b'foreign')])) )
        self.assertTrue(authenticated(request([(b'x-harness-session', token.encode())])))
        self.assertTrue(authenticated(request([(b'cookie', ('harness_session=' + token).encode())])))
        self.assertEqual(session_token(), token)

    async def test_browser_api_rejects_missing_credential_in_daily_mode(self):
        import httpx
        from bff.app import app
        from core.ui_session import session_token
        with patch.dict(os.environ, {'DAILY_SERVICES': '1'}):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://127.0.0.1:9510') as client:
                response = await client.get('/api/workspaces')
                self.assertEqual(response.status_code, 403)
                with patch('bff.app.get_chat_service', return_value=self.service):
                    response = await client.get('/api/workspaces', headers={'X-Harness-Session': session_token()})
                    self.assertEqual(response.status_code, 200)
                    self.assertIn('HttpOnly', response.headers['set-cookie'])
                    self.assertEqual((await client.get('/api/workspaces')).status_code, 200)


if __name__ == '__main__':
    unittest.main()
