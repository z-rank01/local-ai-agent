"""Packaged-provider integration: incremental sources, locations and isolation."""
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch
import httpx
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'skills/files'))
from plugin_host import PluginHost
from path_guard import PathGuard
from harness.contracts.tool_provider import ExecutionContext
from harness.capabilities.packaged_plugins import register_packages
from core.conversation_store import ConversationStore
from core.tool_registry import ToolRegistry
from core import config


class DocumentIndexFlowTests(unittest.TestCase):
    def test_incremental_index_returns_real_locations_and_rejects_stale_sources(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            docs = root / 'docs'; docs.mkdir()
            source = docs / '纪要.md'
            source.write_text('第一段：会议安排\n\n第二段：搜索插件按权限启停，新权限不自动放开。', encoding='utf-8')
            original = source.read_bytes()
            host = PluginHost(ROOT / 'plugins', ExecutionContext('test', root, PathGuard(str(root)).resolve))
            args = {'directory': str(docs)}
            self.assertEqual(host.invoke('documents_find', {**args, 'query': '权限'})['status'], 'NOT_INDEXED')
            receipt = host.invoke('documents_index', args)
            self.assertEqual((receipt['status'], receipt['indexed']), ('COMPLETE', 1))
            self.assertEqual(host.invoke('documents_index', args)['unchanged'], 1)
            matches = host.invoke('documents_find', {**args, 'query': '权限'})['matches']
            self.assertEqual(matches[0]['paragraph'], 2)
            self.assertEqual(matches[0]['path'], str(source))
            self.assertEqual(source.read_bytes(), original)
            paraphrase = host.invoke('documents_find', {**args, 'query': '新权限不能自动放开'})
            self.assertEqual(paraphrase['matches'][0]['paragraph'], 2)
            self.assertIn('权限', paraphrase['matches'][0]['matched_terms'])
            self.assertLess(paraphrase['matches'][0]['match_score'], 1)
            source.write_text('新版：权限按工作区隔离。', encoding='utf-8')
            stale = host.invoke('documents_find', {**args, 'query': '权限'})
            self.assertEqual(stale['status'], 'STALE')
            self.assertEqual(stale['matches'], [])
            host.invoke('documents_index', args)
            updated = host.invoke('documents_find', {**args, 'query': '权限'})['matches'][0]
            self.assertNotEqual(updated['source_version'], matches[0]['source_version'])
            (docs / 'invalid.txt').write_bytes(b'\xff\xfe\x00')
            self.assertEqual(host.invoke('documents_index', args)['status'], 'PARTIAL')
            source.unlink()
            self.assertEqual(host.invoke('documents_index', args)['removed'], 1)
            self.assertEqual(host.invoke('documents_find', {**args, 'query': '权限'})['matches'], [])
            with self.assertRaises(PermissionError):
                host.invoke('documents_index', {'directory': str(root.parent)})

    def test_pdf_page_is_retained(self):
        try:
            import fitz
        except ImportError:
            self.skipTest('PDF extraction is additionally verified in the built file-service image')
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with fitz.open() as doc:
                doc.new_page().insert_text((72, 72), 'First page')
                doc.new_page().insert_text((72, 72), 'Second page source evidence')
                doc.save(root / 'source.pdf')
            host = PluginHost(ROOT / 'plugins', ExecutionContext('test', root, PathGuard(str(root)).resolve))
            host.invoke('documents_index', {'directory': str(root)})
            result = host.invoke('documents_find', {'directory': str(root), 'query': 'evidence'})
            self.assertEqual(result['matches'][0]['page'], 2)


class PackagedLifecycleFlowTests(unittest.IsolatedAsyncioTestCase):
    async def test_activation_requires_exact_deployed_resources_and_disable_preserves_host_tools(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            context = ExecutionContext('test', root, PathGuard(str(root)).resolve)
            host = PluginHost(ROOT / 'plugins', context)
            runtime = SimpleNamespace(capabilities=None, router=SimpleNamespace(),
                tool_registry=ToolRegistry(config.TOOLS_DIR), store=ConversationStore(root / 'conversations.db'))
            register_packages(runtime, ROOT / 'plugins', 'http://files', {})
            self.assertNotIn('documents_index', runtime.tool_registry.known_tools)
            payload = {'versions': host.versions, 'revisions': {}}
            transport = httpx.MockTransport(lambda req: httpx.Response(200, json=payload))
            client_type = httpx.AsyncClient
            def client(**kwargs):
                return client_type(transport=transport, **kwargs)
            with patch('harness.capabilities.packaged_plugins.httpx.AsyncClient', side_effect=client):
                with self.assertRaisesRegex(ValueError, '版本不一致'):
                    await runtime.capabilities.activate('document-index')
                self.assertNotIn('documents_index', runtime.tool_registry.known_tools)
                payload['revisions'] = host.revisions
                await runtime.capabilities.activate('document-index')
            self.assertEqual(runtime.tool_registry.get_owner('documents_find'), 'document-index')
            await runtime.capabilities.deactivate('document-index')
            self.assertNotIn('documents_find', runtime.tool_registry.known_tools)
            self.assertIn('file_read', runtime.tool_registry.known_tools)
