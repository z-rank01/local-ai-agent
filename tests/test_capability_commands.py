"""Declared commands use the original workspace tool boundary."""
import os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx
import yaml

from core import config
from harness.runtime.commands import CommandRegistry
from tests.test_in1 import ServiceTests


class CapabilityCommandTests(ServiceTests):
    async def test_declared_command_uses_original_workspace_tool_route(self):
        self.runtime.commands = CommandRegistry.bundled(config._PROJECT_ROOT / 'plugins')
        definition = yaml.safe_load((Path(config._PROJECT_ROOT) /
            'plugins/document-index/tools/documents_index.yaml').read_text(encoding='utf-8'))
        self.runtime.tool_registry.replace_owner('document-index', [definition])
        dispatch = AsyncMock(return_value={'status': 'COMPLETE', 'indexed': 2})
        self.runtime.router.dispatch = dispatch
        result = await self.service.execute_capability_command(
            'document-index', 'reindex', {'directory': 'docs'}, 'default')
        self.assertEqual(result['result']['indexed'], 2)
        self.assertEqual(dispatch.await_args.args, ('documents_index', {'directory': 'docs'}, 'default'))
        self.assertFalse(self.service.workspaces.active)
        with self.assertRaisesRegex(ValueError, '未声明参数'):
            await self.service.execute_capability_command(
                'document-index', 'reindex', {'command': 'not-allowed'}, 'default')
        self.assertEqual(dispatch.await_count, 1)

    async def test_command_http_requires_session_and_local_origin(self):
        from bff.app import app
        service = SimpleNamespace(execute_capability_command=AsyncMock(
            return_value={'plugin': 'document-index', 'command': 'reindex', 'result': {'status': 'COMPLETE'}}))
        with patch.dict(os.environ, {'DAILY_SERVICES': '1'}), \
                patch('core.ui_session.session_token', return_value='test-page-token'), \
                patch('bff.deps.get_chat_service', return_value=service):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                    base_url='http://127.0.0.1:9510') as client:
                denied = await client.post('/api/capabilities/document-index/commands/reindex', json={})
                self.assertEqual(denied.status_code, 403)
                allowed = await client.post('/api/capabilities/document-index/commands/reindex',
                    json={'workspace_id': 'default', 'arguments': {'directory': 'docs'}},
                    headers={'X-Harness-Session': 'test-page-token', 'Origin': 'http://127.0.0.1:9510'})
                self.assertEqual(allowed.status_code, 200)
        self.assertEqual(service.execute_capability_command.await_args.args,
                         ('document-index', 'reindex', {'directory': 'docs'}, 'default'))
