"""Cloud models get read tools with the workspace grant, writes only with the second."""
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import yaml

from core import config, model_settings
from core.agent import Agent
from core.tool_registry import ToolRegistry

EXPECTED_READ = {
    'file_read', 'file_list', 'file_convert', 'web_search', 'web_fetch',
    'conversation_read', 'conversation_search', 'git_status',
    'package_list', 'package_status', 'skill_list', 'skill_info',
}
EXPECTED_WRITE = {
    'file_write', 'file_edit', 'file_delete', 'file_rename', 'code_exec',
    'shell_exec', 'git_commit', 'pip_install', 'package_cancel',
    'skill_register', 'skill_update', 'skill_unregister', 'skill_run',
}


class DeclarationTests(unittest.TestCase):
    def _tools(self):
        out = {}
        for path in sorted(Path(config.TOOLS_DIR).glob('*.yaml')):
            tool = yaml.safe_load(path.read_text(encoding='utf-8'))
            out[tool['name']] = tool
        return out

    def test_every_tool_declares_a_cloud_tier(self):
        missing = [name for name, tool in self._tools().items() if tool.get('cloud') not in ('read', 'write')]
        self.assertEqual(missing, [], 'tools must declare cloud: read|write')

    def test_read_tier_covers_the_expected_tools(self):
        tools = self._tools()
        read = {name for name, tool in tools.items() if tool.get('cloud') == 'read'}
        self.assertTrue(EXPECTED_READ <= read, EXPECTED_READ - read)

    def test_write_tier_covers_the_expected_tools(self):
        tools = self._tools()
        write = {name for name, tool in tools.items() if tool.get('cloud') == 'write'}
        self.assertTrue(EXPECTED_WRITE <= write, EXPECTED_WRITE - write)

    def test_undeclared_tools_default_to_write(self):
        registry = ToolRegistry(config.TOOLS_DIR)
        registry._tools['hypothetical-new-tool'] = {'name': 'hypothetical-new-tool',
                                                    'backend': 'skill-files'}
        self.assertEqual(registry.get_cloud_tier('hypothetical-new-tool'), 'write')

    def test_read_only_definitions_exclude_write_tools(self):
        registry = ToolRegistry(config.TOOLS_DIR, enable_websearch=True,
                                stock_bridge_url=config.STOCK_BRIDGE_URL or 'http://127.0.0.1:1')
        names = {d['function']['name'] for d in registry.get_definitions(allow_write=False)}
        self.assertIn('file_read', names)
        self.assertIn('web_search', names)
        self.assertIn('stock_report_read', names)
        self.assertFalse(names & EXPECTED_WRITE, names & EXPECTED_WRITE)

    def test_full_definitions_include_write_tools(self):
        registry = ToolRegistry(config.TOOLS_DIR, enable_websearch=True)
        names = {d['function']['name'] for d in registry.get_definitions(allow_write=True)}
        self.assertEqual(names & EXPECTED_WRITE, EXPECTED_WRITE)


class _Model:
    cloud = True

    def __init__(self, parent):
        self.parent = parent

    async def chat_stream_with_tools(self, messages, tools=None):
        self.parent.seen_tools = tools
        yield '', {'role': 'assistant', 'content': 'ok'}


class AgentTierTests(unittest.IsolatedAsyncioTestCase):
    def agent(self, definitions):
        parent = self

        class Model(_Model):
            def __init__(self):
                super().__init__(parent)

        async def process(messages):
            return messages

        class Router:
            async def dispatch_stream(self, *args):
                raise AssertionError('no tool call expected')
                yield  # pragma: no cover

        self.seen_tools = None
        return Agent(llm=Model(), router=Router(),
                     registry=SimpleNamespace(get_definitions=lambda **kw: list(definitions)),
                     audit=SimpleNamespace(record=lambda *a: None),
                     context_mgr=SimpleNamespace(process=process),
                     prompt_builder=SimpleNamespace(build=lambda **kw: 'system'), max_rounds=1)

    READ_DEF = {'type': 'function', 'function': {'name': 'file_read'}}
    WRITE_DEF = {'type': 'function', 'function': {'name': 'code_exec'}}

    async def test_cloud_read_grant_cannot_call_write_tools(self):
        agent = self.agent([])  # registry already withheld the write tool
        agent.workspace_cloud_allowed = True
        agent.cloud_write_allowed = False
        call = {'id': 'c1', 'type': 'function',
                'function': {'name': 'code_exec', 'arguments': '{"code":"print(1)"}'}}
        agent.llm.chat_stream_with_tools = lambda messages, tools=None: _reply(call)
        events = [e async for e in agent.run([{'role': 'user', 'content': 'run it'}])]
        ends = [e for e in events if e.kind == 'tool_end']
        self.assertEqual(len(ends), 1)
        self.assertEqual(ends[0].data['status'], 'error')
        self.assertIn('未开放', json.dumps(ends[0].data['result'], ensure_ascii=False))

    async def test_cloud_without_grant_gets_no_tools_and_is_told_why(self):
        agent = self.agent([])
        agent.workspace_cloud_allowed = False
        agent.cloud_write_allowed = False
        async for _ in agent.run([{'role': 'user', 'content': 'read my files'}]):
            pass
        prompt = self.seen_tools
        self.assertFalse(prompt)


def _reply(call):
    async def stream():
        yield '', {'role': 'assistant', 'content': '', 'tool_calls': [call]}
    return stream()


class GrantLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / 'settings.json'

    def _patch(self):
        return patch.object(model_settings, 'settings_path', lambda: self.path)

    def test_pre_existing_grant_keeps_write_access(self):
        """A workspace authorised before this feature existed must not regress."""
        key = str(config.WORKSPACE_PATH.resolve())
        self.path.write_text(json.dumps({'workspaces': {key: True}}), encoding='utf-8')
        with self._patch():
            self.assertTrue(model_settings.workspace_allowed())
            self.assertTrue(model_settings.cloud_write_allowed())

    def test_fresh_grant_is_read_only(self):
        with self._patch():
            model_settings.update_settings('m', workspace=True)
            self.assertTrue(model_settings.workspace_allowed())
            self.assertFalse(model_settings.cloud_write_allowed())

    def test_write_grant_is_independent_once_set(self):
        with self._patch():
            model_settings.update_settings('m', workspace=True, cloud_write=True)
            self.assertTrue(model_settings.cloud_write_allowed())
            model_settings.update_settings('m', cloud_write=False)
            self.assertFalse(model_settings.cloud_write_allowed())
            self.assertTrue(model_settings.workspace_allowed(), 'read grant must survive')

    def test_revoking_read_revokes_write(self):
        with self._patch():
            model_settings.update_settings('m', workspace=True, cloud_write=True)
            model_settings.update_settings('m', workspace=False)
            self.assertFalse(model_settings.workspace_allowed())
            self.assertFalse(model_settings.cloud_write_allowed())
            # Re-authorising later must not silently restore writes.
            model_settings.update_settings('m', workspace=True)
            self.assertFalse(model_settings.cloud_write_allowed())


if __name__ == '__main__':
    unittest.main()
