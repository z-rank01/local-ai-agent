"""A new provider uses ordinary policy, workspace checks and lifecycle leases."""
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

from core import config
from core.audit_logger import AuditLogger
from core.policy_engine import PolicyEngine
from core.tool_registry import ToolRegistry
from core.tool_router import ToolRouter
from harness.runtime.lifecycle import LifecycleManager


class ProviderRoutingTests(unittest.IsolatedAsyncioTestCase):
    async def test_registered_provider_obeys_host_boundaries_and_can_be_removed(self):
        with tempfile.TemporaryDirectory() as directory:
            registry = ToolRegistry(config.TOOLS_DIR)
            registry.replace_owner('example', [{'name': 'example_lookup', 'backend': 'example-service'}])
            provider = SimpleNamespace(call_tool=AsyncMock(return_value={'status': 'COMPLETE', 'value': 7}))
            router = ToolRouter('http://files', 'http://runner', 'http://search',
                                PolicyEngine(config.POLICY_PATH), AuditLogger(Path(directory) / 'audit.jsonl'), registry)
            self.addAsyncCleanup(router.close)
            router.bind_provider('example-service', provider)
            router.workspace_validate = AsyncMock(side_effect=PermissionError('workspace revoked'))
            with self.assertRaisesRegex(PermissionError, 'revoked'):
                await router.dispatch('example_lookup', {}, 'conversation')
            provider.call_tool.assert_not_awaited()
            router.workspace_validate.side_effect = None
            lifecycle = LifecycleManager()
            lifecycle.register('example', SimpleNamespace(activate=AsyncMock(), deactivate=AsyncMock()))
            router.capabilities = lifecycle
            with self.assertRaises(RuntimeError):
                await router.dispatch('example_lookup', {}, 'conversation')
            await lifecycle.activate('example')
            result = await router.dispatch('example_lookup', {'query': 'seven'}, 'conversation')
            self.assertEqual(result['value'], 7)
            provider.call_tool.assert_awaited_once_with('example_lookup', {'query': 'seven'}, 'conversation')
            await lifecycle.deactivate('example')
            router.bind_provider('example-service', None)
            registry.unregister_owner('example')
            self.assertIn('file_read', registry.known_tools)
            with self.assertRaisesRegex(ValueError, 'Unknown tool'):
                await router.dispatch('example_lookup', {}, 'conversation')
