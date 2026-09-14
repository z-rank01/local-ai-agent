import asyncio
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx
from core.stock_service import StockService
from core.tool_registry import ToolRegistry
from core import config


class StockServiceTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        (self.root / 'scripts').mkdir()
        (self.root / 'scripts/run_paper.py').write_text('')
        self.service = StockService()
        self.service.settings_path = self.root / 'settings.json'
        self.service.settings = {'root': str(self.root), 'state_dir': 'simulation', 'port': 18765, 'enabled': False, 'worker': False}

    def test_paths_and_settings_are_bounded_and_persisted(self):
        self.service.save()
        assert self.service.read_settings() == self.service.settings
        self.service.settings['state_dir'] = '../foreign'
        with self.assertRaises(ValueError): self.service.paths()

    async def test_attach_updates_shared_registry_and_real_router(self):
        state = self.root / 'simulation'; state.mkdir()
        (state / '.paper-master-token').write_text('test-token')
        registry = ToolRegistry(config.TOOLS_DIR, stock_bridge_url='')
        runtime = SimpleNamespace(stock_bridge=None, router=SimpleNamespace(_stock_bridge=None), tool_registry=registry)
        await self.service.attach(runtime, True)
        self.assertIs(runtime.stock_bridge, runtime.router._stock_bridge)
        self.assertIn('stock_research_submit', registry.known_tools)
        await self.service.attach(runtime, False)
        self.assertNotIn('stock_research_submit', registry.known_tools)
        self.assertIsNone(runtime.router._stock_bridge)

    async def test_busy_stop_keeps_bridge_and_settings(self):
        self.service.settings['enabled'] = True
        self.service.request = AsyncMock(side_effect=[{}, ValueError('正在收尾')])
        self.service.attach = AsyncMock()
        with self.assertRaisesRegex(ValueError, '收尾'):
            await self.service.action(None, {'action': 'stop'})
        self.assertTrue(self.service.settings['enabled'])
        self.service.attach.assert_not_awaited()

    async def test_active_chat_prevents_mutation(self):
        with self.assertRaisesRegex(ValueError, '聊天'):
            await self.service.action(None, {'action': 'start'}, lambda: True)

    async def test_control_rejects_foreign_origin(self):
        from bff.app import app
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app, client=('127.0.0.1', 1)), base_url='http://test') as c:
            for headers in ({}, {'Origin': 'https://foreign.invalid'}):
                r = await c.post('/api/admin/stock-service', headers=headers, json={'action': 'start'})
                self.assertEqual(r.status_code, 403)

    async def test_safe_shutdown_does_not_kill_while_draining(self):
        from bff import app as module
        from core.stock_service import stock_service
        with patch.object(stock_service, 'status', AsyncMock(return_value={'online': True})), \
             patch.object(stock_service, 'request', AsyncMock(side_effect=ValueError('正在收尾'))), \
             patch.object(module, '_stack_shutdown_targets', return_value={'bff': [1], 'web': [2]}), \
             patch.object(module, 'schedule_stack_shutdown') as kill:
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=module.app, client=('127.0.0.1', 1)), base_url='http://test') as c:
                r = await c.post('/api/admin/shutdown-stack')
                self.assertEqual(r.status_code, 409)
                kill.assert_not_called()
