"""A frozen discovery catalog must not gate already-authorized file tools."""
import unittest
from types import SimpleNamespace

from core.agent import Agent
from harness.capabilities.skill_session import SkillSession
from harness.runtime.turns import NullTurn
from plugins.finance.turn import FinanceTurn


class DiscoveryExecutionTests(unittest.IsolatedAsyncioTestCase):
    async def execute(self, turn, *, cloud=False, authorized=True):
        offered, executed = [], []

        class Model:
            async def chat_stream_with_tools(self, messages, tools):
                offered.append([d['function']['name'] for d in tools or []])
                if len(offered) == 1:
                    yield '', {'role': 'assistant', 'content': '', 'tool_calls': [{
                        'id': 'read-report', 'function': {'name': 'file_read',
                            'arguments': {'path': '/workspace/reports/saved/report.md'}}}]}
                else:
                    yield '', {'role': 'assistant', 'content': '已保存报告的正文。'}

        class Router:
            skill_session = SkillSession(None, '', {}, {'versions': {
                'meeting-notes': {'name': 'meeting-notes', 'revision': 'frozen'}}, 'invalid': []})

            async def dispatch(self, name, params, session):
                executed.append((name, params))
                return {'content': '已保存报告的正文。'}

        async def process(messages):
            return messages

        model = Model()
        model.cloud = cloud
        agent = Agent(llm=model, router=Router(),
            registry=SimpleNamespace(get_definitions=lambda **kw: [
                {'type': 'function', 'function': {'name': name}}
                for name in ('file_read', 'skill_info', 'file_list')], create_turn=lambda: turn),
            audit=SimpleNamespace(record=lambda *args: None),
            context_mgr=SimpleNamespace(process=process),
            prompt_builder=SimpleNamespace(build=lambda **kw: 'test'))
        agent.workspace_cloud_allowed = authorized
        agent.quality_first_research = agent.turn_loop_enabled = isinstance(turn, FinanceTurn)
        events = [event async for event in agent.run([{'role': 'user',
            'content': '请回读已保存报告，不另建研究，也不要选用方法包。'}])]
        return offered, executed, events

    async def test_generic_file_read_does_not_require_skill_discovery(self):
        offered, executed, events = await self.execute(NullTurn())
        self.assertIn('file_read', offered[0])
        self.assertEqual([params['path'] for name, params in executed if name == 'file_read'],
                         ['/workspace/reports/saved/report.md'])
        self.assertNotIn('skill_info', [name for name, _ in executed])
        self.assertEqual(events[-1].kind, 'done')

    async def test_finance_followup_can_read_without_starting_research_or_skill(self):
        offered, executed, events = await self.execute(FinanceTurn())
        self.assertIn('file_read', offered[0])
        self.assertEqual([params['path'] for name, params in executed if name == 'file_read'],
                         ['/workspace/reports/saved/report.md'])
        self.assertNotIn('skill_info', [name for name, _ in executed])
        self.assertEqual(events[-1].kind, 'done')

    async def test_discovery_does_not_grant_cloud_workspace_access(self):
        offered, executed, _ = await self.execute(FinanceTurn(), cloud=True, authorized=False)
        self.assertEqual(offered[0], [])
        self.assertEqual(executed, [])
