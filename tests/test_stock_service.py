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

    async def test_stop_waits_for_delayed_exit_and_returns_offline(self):
        server = await asyncio.start_server(lambda r, w: w.close(), '127.0.0.1', 0)
        self.service.settings.update(port=server.sockets[0].getsockname()[1], enabled=True)
        self.service.request = AsyncMock(side_effect=[{}, {'status': 'shutting_down'}])
        self.service.attach = AsyncMock()
        closing = asyncio.create_task(self.service.action(None, {'action': 'stop'}))
        try:
            await asyncio.sleep(0.2)
            self.assertFalse(closing.done(), 'Must wait beyond the shutdown acknowledgement')
            self.service.attach.assert_not_awaited()
            server.close()
            await server.wait_closed()
            result = await asyncio.wait_for(closing, 8)
            self.assertFalse(result['online'])
            self.assertFalse(self.service.read_settings()['enabled'])
            self.service.attach.assert_awaited_once_with(None, False)
        finally:
            server.close()
            await server.wait_closed()
            if not closing.done():
                closing.cancel()
                await asyncio.gather(closing, return_exceptions=True)

    async def test_stop_already_offline_is_idempotent(self):
        self.service.settings['enabled'] = True
        self.service.request = AsyncMock(side_effect=httpx.ConnectError('refused'))
        self.service.attach = AsyncMock()
        for _ in range(2):
            result = await self.service.action(None, {'action': 'stop'})
            self.assertFalse(result['online'])
            self.assertFalse(result['settings']['enabled'])

    async def test_stop_does_not_treat_timeout_or_wrong_workspace_as_offline(self):
        for error in (httpx.ReadTimeout('timeout'), ValueError('另一个状态目录')):
            self.service.settings['enabled'] = True
            self.service.request = AsyncMock(side_effect=error)
            self.service.attach = AsyncMock()
            with self.assertRaises(type(error)):
                await self.service.action(None, {'action': 'stop'})
            self.service.attach.assert_not_awaited()
            self.assertTrue(self.service.settings['enabled'])

    async def test_stop_timeout_does_not_report_success(self):
        self.service.settings['enabled'] = True
        self.service.request = AsyncMock(side_effect=[{}, {'status': 'shutting_down'}])
        self.service.wait_until_stopped = AsyncMock(side_effect=asyncio.TimeoutError)
        self.service.attach = AsyncMock()
        with self.assertRaisesRegex(ValueError, '尚未退出'):
            await self.service.action(None, {'action': 'stop'})
        self.service.attach.assert_not_awaited()
        self.assertTrue(self.service.settings['enabled'])

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
