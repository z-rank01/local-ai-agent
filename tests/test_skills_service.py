"""Skill switch service tests: registry flip and persistence (no Docker, no network)."""

import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from core import config
from core.skills_service import SkillSwitches
from core.tool_registry import ToolRegistry


def fake_runtime(enabled: bool = False):
    registry = ToolRegistry(config.TOOLS_DIR, enable_websearch=enabled, stock_bridge_url='')
    router = SimpleNamespace(_backend_urls={'skill-files': 'http://x', 'skill-runner': 'http://y'},
                             _stock_bridge=None)
    return SimpleNamespace(tool_registry=registry, router=router)


class SkillSwitchesTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.settings = Path(self.tmp.name) / 'skill-switches.json'

    def make_service(self, saved=None, env_flag=False):
        if saved is not None:
            self.settings.write_text(json.dumps(saved), encoding='utf-8')
        with patch.dict(os.environ, {'SKILL_SWITCHES': str(self.settings)}), \
                patch.object(config, 'ENABLE_WEBSEARCH', env_flag):
            return SkillSwitches()

    def test_env_flag_seeds_default_when_no_saved_setting(self):
        svc = self.make_service(saved=None, env_flag=True)
        self.assertTrue(svc.websearch_enabled)
        svc2 = self.make_service(saved=None, env_flag=False)
        self.assertFalse(svc2.websearch_enabled)

    def test_saved_setting_wins_over_env_flag(self):
        svc = self.make_service(saved={'websearch': True}, env_flag=False)
        self.assertTrue(svc.websearch_enabled)
        svc = self.make_service(saved={'websearch': False}, env_flag=True)
        self.assertFalse(svc.websearch_enabled)

    def test_invalid_settings_file_falls_back_to_env_default(self):
        self.settings.write_text('{broken json', encoding='utf-8')
        svc = self.make_service(saved=None, env_flag=False)
        self.assertFalse(svc.websearch_enabled)

    def test_save_persists_toggle(self):
        svc = self.make_service(saved=None, env_flag=False)
        svc.websearch_enabled = True
        svc.save()
        self.assertEqual(json.loads(self.settings.read_text(encoding='utf-8')), {'websearch': True})

    def test_apply_to_flips_registry_and_router_urls(self):
        runtime = fake_runtime(enabled=False)
        svc = self.make_service(saved=None, env_flag=False)
        self.assertNotIn('web_search', runtime.tool_registry.known_tools)
        svc.apply_to(runtime, True)
        self.assertIn('web_search', runtime.tool_registry.known_tools)
        self.assertIn('web_fetch', runtime.tool_registry.known_tools)
        self.assertIn('skill-websearch', runtime.router._backend_urls)
        svc.apply_to(runtime, False)
        self.assertNotIn('web_search', runtime.tool_registry.known_tools)
        self.assertNotIn('skill-websearch', runtime.router._backend_urls)
        self.assertIn('skill-files', runtime.router._backend_urls)  # untouched

    def test_apply_to_preserves_stock_tools(self):
        runtime = fake_runtime(enabled=False)
        bridge = SimpleNamespace(_base_url='http://127.0.0.1:8765')
        runtime.router._stock_bridge = bridge
        svc = self.make_service(saved=None, env_flag=True)
        svc.apply_to(runtime, False)
        self.assertNotIn('web_search', runtime.tool_registry.known_tools)
        self.assertIn('stock_account_view', runtime.tool_registry.known_tools)

    def test_set_websearch_enable_starts_containers_then_flips(self):
        runtime = fake_runtime(enabled=False)
        svc = self.make_service(saved=None, env_flag=False)
        calls = []
        async def fake_compose(*args, **kwargs):
            calls.append(args)
        async def fake_wait():
            return None
        with patch.object(svc, '_compose', fake_compose), patch.object(svc, '_wait_websearch', fake_wait):
            status = asyncio_run(svc.set_websearch(runtime, True))
        self.assertEqual(calls, [('up', '-d')])
        self.assertTrue(status['websearch']['active'])
        self.assertTrue(svc.websearch_enabled)
        self.assertEqual(json.loads(self.settings.read_text(encoding='utf-8')), {'websearch': True})

    def test_set_websearch_disable_flips_then_stops_containers(self):
        runtime = fake_runtime(enabled=True)
        svc = self.make_service(saved={'websearch': True})
        calls = []
        async def fake_compose(*args, **kwargs):
            calls.append(args)
        async def fake_wait():
            return None
        with patch.object(svc, '_compose', fake_compose), patch.object(svc, '_wait_websearch', fake_wait):
            status = asyncio_run(svc.set_websearch(runtime, False))
        self.assertEqual(calls, [('stop',)])
        self.assertFalse(status['websearch']['active'])

    def test_set_websearch_refuses_while_chat_active(self):
        runtime = fake_runtime(enabled=False)
        svc = self.make_service(saved=None, env_flag=False)
        with self.assertRaises(ValueError):
            asyncio_run(svc.set_websearch(runtime, True, active_check=lambda: True))
        self.assertFalse(svc.websearch_enabled)

    def test_set_websearch_noop_when_already_in_state(self):
        runtime = fake_runtime(enabled=False)
        svc = self.make_service(saved=None, env_flag=False)
        calls = []
        async def fake_compose(*args, **kwargs):
            calls.append(args)
        with patch.object(svc, '_compose', fake_compose):
            asyncio_run(svc.set_websearch(runtime, False))
        self.assertEqual(calls, [])


def asyncio_run(coro):
    import asyncio
    return asyncio.run(coro)


if __name__ == '__main__':
    unittest.main()
