"""Prefetch delivery must respect the cloud workspace grant (IN1.4 boundary)."""
import os
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from core import config
from core.agent import Agent

SECRET = 'WORKSPACE-SECRET-CONTENT-abcdef'
FILE_NAME = 'quarterly-report.txt'


class _Model:
    cloud = True

    def __init__(self, parent):
        self.parent = parent

    async def chat_stream_with_tools(self, messages, tools=None):
        self.parent.inputs.append([dict(m) for m in messages])
        yield '', {'role': 'assistant', 'content': '好的'}


class _Router:
    """Answers file_list/file_read the way the real router would."""

    def __init__(self, parent, names=None, content=None):
        self.parent = parent
        self.names = names or [FILE_NAME]
        self.content = content

    async def dispatch(self, tool, params, session_id='default'):
        self.parent.dispatches.append((tool, params.get('directory') or params.get('path')))
        if tool == 'file_list':
            if params.get('directory') != '/workspace/docs':
                return {'entries': []}
            return {'entries': [{'name': name, 'type': 'file', 'size': 0} for name in self.names]}
        if tool == 'file_read':
            body = self.content if self.content is not None else SECRET
            return {'content': body, 'sha256': 'x' * 64, 'path': params.get('path')}
        raise AssertionError(f'unexpected tool {tool}')

    async def dispatch_stream(self, *args):
        raise AssertionError('no tool calls expected in these tests')


class PrefetchBudgetTests(unittest.IsolatedAsyncioTestCase):
    """A prefetched read must never look like the whole file when it is not."""

    def agent(self):
        parent = self

        class Model(_Model):
            def __init__(self):
                super().__init__(parent)

        class Router(_Router):
            def __init__(self):
                super().__init__(parent)

        async def process(messages):
            return messages

        self.inputs, self.dispatches = [], []
        return Agent(llm=Model(), router=Router(),
                     registry=SimpleNamespace(get_definitions=lambda **kw: []),
                     audit=SimpleNamespace(record=lambda *a: None),
                     context_mgr=SimpleNamespace(process=process),
                     prompt_builder=SimpleNamespace(build=lambda **kw: 'system'), max_rounds=2)

    async def _run_prefetch(self, agent, query):
        return await agent._prefetch_file_context(query, 'session')

    async def test_small_file_is_injected_whole(self):
        agent = self.agent()
        with patch.object(config, 'PREFETCH_MAX_CHARS', 60_000):
            out = await self._run_prefetch(agent, f'看一下 {FILE_NAME}')
        self.assertIn(SECRET, out)
        self.assertNotIn('尚未读完', out)

    async def test_oversized_file_states_total_and_how_to_continue(self):
        body = ''.join(f'row-{i:05d}\n' for i in range(4000))
        agent = self.agent()
        agent.router.content = body
        with patch.object(config, 'PREFETCH_MAX_CHARS', 5_000):
            out = await self._run_prefetch(agent, f'看一下 {FILE_NAME}')
        self.assertIn('尚未读完', out)
        self.assertIn(f'文件共 {len(body)} 字符', out)
        self.assertIn('offset=5000', out)
        self.assertIn('file_read', out)
        # The model must be told the page is partial, not merely that it was cut.
        self.assertNotIn('...[截断]', out)

    async def test_budget_covers_a_real_workspace_file_entirely(self):
        """The regression that started this: a 20k CSV must arrive complete."""
        csv = Path(config.WORKSPACE_PATH) / 'data' / '600519_daily.csv'
        if not csv.exists():
            self.skipTest('workspace CSV not present')
        body = csv.read_text(encoding='utf-8')
        from core.agent import _format_prefetch_content
        out = _format_prefetch_content(csv.name, body, config.PREFETCH_MAX_CHARS)
        self.assertNotIn('尚未读完', out)
        self.assertIn(body, out)

    async def test_budget_is_shared_across_files(self):
        names = ['a.txt', 'b.txt', 'c.txt']
        parent = self

        class Router(_Router):
            def __init__(self):
                super().__init__(parent, names=names, content='x' * 1000)

        agent = self.agent()
        agent.router = Router()
        with patch.object(config, 'PREFETCH_MAX_CHARS', 1_500):
            out = await self._run_prefetch(agent, '看一下 a.txt 和 b.txt 和 c.txt')
        # 1500 chars of budget, capped at 3 files: the third must not be silently
        # over-filled, and nothing may exceed the shared budget by much.
        self.assertLessEqual(len(out), 1_500 + 600)
        self.assertIn('尚未读完', out)

    async def test_zero_budget_injects_nothing(self):
        agent = self.agent()
        with patch.object(config, 'PREFETCH_MAX_CHARS', 0):
            out = await self._run_prefetch(agent, f'看一下 {FILE_NAME}')
        self.assertIsNone(out)


class PrefetchGateTests(unittest.IsolatedAsyncioTestCase):
    def agent(self):
        parent = self

        class Model(_Model):
            def __init__(self):
                super().__init__(parent)

        class Router(_Router):
            def __init__(self):
                super().__init__(parent)

        async def process(messages):
            return messages

        self.inputs, self.dispatches = [], []
        return Agent(llm=Model(), router=Router(),
                     registry=SimpleNamespace(get_definitions=lambda **kw: []),
                     audit=SimpleNamespace(record=lambda *a: None),
                     context_mgr=SimpleNamespace(process=process),
                     prompt_builder=SimpleNamespace(build=lambda **kw: 'system'), max_rounds=2)

    def _plain(self, agent):
        """Render every model-visible message into one searchable string."""
        return '\n'.join(str(m.get('content', '')) for turn in self.inputs for m in turn)

    async def test_cloud_without_grant_never_receives_local_file_content(self):
        agent = self.agent()
        agent.workspace_cloud_allowed = False
        with patch.object(config, 'WORKSPACE_CLOUD_ALLOWED', False):
            async for _ in agent.run([{'role': 'user', 'content': f'帮我分析这份文档 {FILE_NAME}'}]):
                pass
        self.assertNotIn(SECRET, self._plain(agent))
        self.assertEqual(self.dispatches, [], 'no workspace tool may run without the grant')

    async def test_cloud_with_grant_receives_the_file_content(self):
        agent = self.agent()
        agent.workspace_cloud_allowed = True
        with patch.object(config, 'WORKSPACE_CLOUD_ALLOWED', False):
            async for _ in agent.run([{'role': 'user', 'content': f'帮我分析这份文档 {FILE_NAME}'}]):
                pass
        self.assertIn(SECRET, self._plain(agent))
        self.assertIn(('file_list', '/workspace/docs'), self.dispatches)
        self.assertIn(('file_read', f'/workspace/docs/{FILE_NAME}'), self.dispatches)

    async def test_local_model_receives_the_file_content_without_a_grant(self):
        agent = self.agent()
        agent.llm.cloud = False
        agent.workspace_cloud_allowed = False
        with patch.object(config, 'WORKSPACE_CLOUD_ALLOWED', False):
            async for _ in agent.run([{'role': 'user', 'content': f'帮我分析这份文档 {FILE_NAME}'}]):
                pass
        self.assertIn(SECRET, self._plain(agent))

    async def test_unrelated_turn_prefetches_nothing(self):
        agent = self.agent()
        agent.workspace_cloud_allowed = True
        async for _ in agent.run([{'role': 'user', 'content': '你好'}]):
            pass
        self.assertEqual(self.dispatches, [])


class TimeoutAlignmentTests(unittest.TestCase):
    """The policy must never advertise a timeout the runner silently shortens."""

    def _env_int(self, name: str) -> int | None:
        for line in (config.PROJECT_ROOT / '.env').read_text(encoding='utf-8').splitlines():
            line = line.strip()
            if line.startswith(f'{name}='):
                try:
                    return int(line.split('=', 1)[1].strip())
                except ValueError:
                    return None
        return None

    def test_policy_ceiling_covers_the_executor_caps(self):
        from core.policy_engine import PolicyEngine
        policy = PolicyEngine(config.POLICY_PATH)
        python_cap = self._env_int('PYTHON_EXEC_TIMEOUT')
        shell_cap = self._env_int('SHELL_EXEC_TIMEOUT')
        self.assertIsNotNone(python_cap, 'PYTHON_EXEC_TIMEOUT missing from .env')
        self.assertIsNotNone(shell_cap, 'SHELL_EXEC_TIMEOUT missing from .env')
        ceiling = policy._max_timeout_seconds
        self.assertGreaterEqual(ceiling, max(python_cap, shell_cap),
                                'policy would let the model plan for a timeout the '
                                'runner silently clamps')
        self.assertGreaterEqual(ceiling, 300)

    def test_policy_accepts_its_own_ceiling_and_rejects_one_more(self):
        from core.policy_engine import PolicyEngine
        policy = PolicyEngine(config.POLICY_PATH)
        ceiling = policy._max_timeout_seconds
        for value in (1, 60, ceiling):
            policy.check('shell_exec', {'command': 'ls', 'timeout': value})
        for bad in (0, -1, ceiling + 1):
            with self.assertRaises(PermissionError):
                policy.check('shell_exec', {'command': 'ls', 'timeout': bad})


if __name__ == '__main__':
    unittest.main()
