"""Functional registration isolation across independent capability lifecycles."""
import unittest
import json
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import os
import httpx
from core import config
from core.tool_registry import ToolRegistry
from harness.capabilities.catalog import package_catalog


class ContextBindingTests(unittest.IsolatedAsyncioTestCase):
    async def test_two_runs_keep_their_workspace_and_model_without_mutating_provider(self):
        from copy import copy
        from core.tool_router import ToolRouter
        from core.policy_engine import PolicyEngine
        from core.audit_logger import AuditLogger
        from harness.contracts.tool_provider import ExecutionContext

        class Bound:
            def __init__(self, context): self.context = context
            async def call_tool(self, tool, arguments, session_id):
                if session_id != self.context.conversation_id:
                    raise PermissionError('conversation mismatch')
                return {'workspace': self.context.workspace_id, 'model': self.context.model,
                        'run': self.context.run_id, 'permissions': sorted(self.context.permissions)}

        class Provider:
            def bind(self, context): return Bound(context)

        with tempfile.TemporaryDirectory() as directory:
            registry = ToolRegistry(Path(directory))
            registry.replace_owner('example', [{'name': 'context_read', 'backend': 'example', 'cloud': 'read'}])
            factory = Provider()
            router = ToolRouter('http://files', 'http://runner', '', PolicyEngine(config.POLICY_PATH),
                AuditLogger(Path(directory) / 'audit.jsonl'), registry, providers={'example': factory})
            self.addAsyncCleanup(router.close)
            first, second = copy(router), copy(router)
            first.bind_context(ExecutionContext('one', Path(directory), Path, 'c1', 'r1', 'local', frozenset({'workspace.read'})))
            second.bind_context(ExecutionContext('two', Path(directory), Path, 'c2', 'r2', 'cloud', frozenset()))
            self.assertIs(router.provider_for('example'), factory)
            self.assertEqual(await first.dispatch('context_read', {}, 'c1'),
                {'workspace': 'one', 'model': 'local', 'run': 'r1', 'permissions': ['workspace.read']})
            self.assertEqual((await second.dispatch('context_read', {}, 'c2'))['workspace'], 'two')
            with self.assertRaises(PermissionError):
                await first.dispatch('context_read', {}, 'c2')


class CapabilityRegistrationTests(unittest.TestCase):
    def test_search_and_finance_changes_preserve_new_plugin(self):
        registry = ToolRegistry(config.TOOLS_DIR)
        tool = {'name': 'documents_find', 'backend': 'documents', 'parameters': {'type': 'object'}}
        registry.replace_owner('documents', [tool])
        for backend in ('skill-websearch', 'stock-bridge'):
            registry.load_backend(config.TOOLS_DIR, backend)
        self.assertIn('web_search', registry.known_tools)
        self.assertIn('stock_report_read', registry.known_tools)
        registry.unregister_owner('skill-websearch')
        self.assertIn('stock_report_read', registry.known_tools)
        registry.unregister_owner('stock-bridge')
        self.assertIn('documents_find', registry.known_tools)
        self.assertIn('file_read', registry.known_tools)
        self.assertEqual(registry.ownership()['documents_find'], 'documents')

    def test_failed_update_is_atomic_and_input_is_not_shared(self):
        registry = ToolRegistry(config.TOOLS_DIR)
        tool = {'name': 'documents_find', 'backend': 'documents', 'parameters': {'type': 'object'}}
        registry.replace_owner('documents', [tool])
        tool['parameters']['type'] = 'array'
        definitions = registry.get_definitions()
        actual = next(x['function'] for x in definitions if x['function']['name'] == 'documents_find')
        self.assertEqual(actual['parameters']['type'], 'object')
        before = registry.known_tools
        with self.assertRaises(ValueError):
            registry.replace_owner('documents', [tool, {'name': 'file_read', 'backend': 'documents'}])
        self.assertEqual(registry.known_tools, before)
        with self.assertRaises(ValueError):
            registry.replace_owner('documents', [tool, tool])
        self.assertEqual(registry.known_tools, before)

    def test_catalog_tracks_actual_registration_and_isolates_bad_manifest(self):
        registry = ToolRegistry(config.TOOLS_DIR)
        rows = package_catalog(config._PROJECT_ROOT / 'plugins', registry.ownership())
        self.assertEqual(next(r for r in rows if r['id'] == 'websearch')['registration'], 'unregistered')
        registry.load_backend(config.TOOLS_DIR, 'skill-websearch')
        rows = package_catalog(config._PROJECT_ROOT / 'plugins', registry.ownership())
        search = next(r for r in rows if r['id'] == 'websearch')
        self.assertEqual(search['registration'], 'registered')
        self.assertIn('web_search', search['tools'])
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            for name, version in [('good', 1), ('bad', 99)]:
                package = root / name
                package.mkdir()
                (package / 'plugin.json').write_text(json.dumps({
                    'id': name, 'harness_api': version, 'version': '1', 'title': name,
                    'description': 'test', 'backends': [], 'required': [], 'optional': [], 'permissions': []
                }), encoding='utf-8')
            result = {row['id']: row for row in package_catalog(root, {})}
            self.assertEqual(result['bad']['registration'], 'invalid')
            self.assertEqual(result['good']['registration'], 'unregistered')


class CapabilityApiTests(unittest.IsolatedAsyncioTestCase):
    async def test_inventory_requires_page_session_and_returns_registered_tools(self):
        from bff.app import app
        registry = ToolRegistry(config.TOOLS_DIR, enable_websearch=True)
        with patch.dict(os.environ, {'DAILY_SERVICES': '1'}), \
                patch('core.ui_session.session_token', return_value='test-page-token'), \
                patch('bff.capabilities.get_runtime', return_value=SimpleNamespace(tool_registry=registry)):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://127.0.0.1:9510') as client:
                denied = await client.get('/api/capabilities')
                self.assertEqual(denied.status_code, 403)
                allowed = await client.get('/api/capabilities', headers={'X-Harness-Session': 'test-page-token'})
                self.assertEqual(allowed.status_code, 200)
                rows = {r['id']: r for r in allowed.json()['capabilities']}
                self.assertIn('web_search', rows['websearch']['tools'])
                self.assertEqual(rows['finance']['registration'], 'unregistered')
