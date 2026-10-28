import os
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch
import httpx


class CapabilityManagementTests(unittest.IsolatedAsyncioTestCase):
    async def test_service_adapters_use_uniform_toggle_and_keep_old_lifecycle_owner(self):
        from bff.app import app
        setter = AsyncMock(return_value={'state': 'INACTIVE', 'accepting': False})
        service = SimpleNamespace(set_enabled=setter)
        runtime = SimpleNamespace(tool_registry=SimpleNamespace(ownership=lambda: {}),
            capability_services={'demo': service}, capabilities=SimpleNamespace(manages=lambda name: name == 'demo'))
        with patch.dict(os.environ, {'DAILY_SERVICES': '1'}), \
             patch('core.ui_session.session_token', return_value='management-test'), \
             patch('bff.capabilities.get_runtime', return_value=runtime), \
             patch('bff.capabilities.package_catalog', return_value=[{'id': 'demo', 'management': 'managed_service'}]), \
             patch('bff.deps.get_chat_service', return_value=SimpleNamespace(_active_conversations=set())):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://127.0.0.1:9510',
                headers={'X-Harness-Session': 'management-test', 'Origin': 'http://127.0.0.1:9510'}) as client:
                response = await client.post('/api/capabilities/demo', json={'enabled': False})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()['state'], 'INACTIVE')
        self.assertIs(setter.call_args.args[0], runtime)
        self.assertFalse(setter.call_args.args[1])
        self.assertFalse(setter.call_args.args[2]())

    async def test_adapter_keeps_its_inflight_error_visible(self):
        from plugins.websearch.service import SkillSwitches
        # No service/network action occurs while a turn is active.
        service = SkillSwitches.__new__(SkillSwitches)
        service.set_websearch = AsyncMock()
        with self.assertRaisesRegex(ValueError, '正在执行'):
            await service.set_enabled(Mock(), False, lambda: True)
        service.set_websearch.assert_not_called()
