from pathlib import Path
from types import SimpleNamespace
from copy import copy
import json
import tempfile
import unittest
from unittest.mock import Mock

from core.subagent_executor import AgentSubagentExecutor, ScopedRegistry
from core.tool_router import ToolRouter
from core.tool_registry import ToolRegistry
from core import config
from harness.contracts.subagents import RoleSpec, RoleTask
from harness.runtime.subagents.spec import role_tasks, load_role_specs
from plugins.finance.research.session import ResearchSession


class CrossPluginScopeTests(unittest.IsolatedAsyncioTestCase):
    def test_foreign_role_policy_cannot_block_host_file_tools_at_startup(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            plugin = root / 'fixture'
            plugin.mkdir()
            metadata = dict(id='fixture', harness_api=1, version='1', title='Fixture',
                            description='scope fixture', backends=[], required=[], optional=[],
                            permissions=[], tool_roles={'parent_only':['file_read']})
            (plugin / 'plugin.json').write_text(json.dumps(metadata), 'utf8')
            host = root / 'subagents.json'
            host.write_text(json.dumps({'agent_roles':[dict(role_id='reader', prompt='Read the file',
                tools=['file_read'], output=dict(artifact_kind='text', recipe_namespace='output'))]}), 'utf8')
            self.assertEqual(load_role_specs(root, {'file_read'}, host)['reader'].tools, {'file_read'})

    async def test_author_analysis_and_review_dispatch_unrelated_provider(self):
        registry = ToolRegistry(config.TOOLS_DIR)
        definitions = [{'name': 'fixture_' + tier, 'backend': 'fixture', 'cloud': tier}
                       for tier in ('read', 'write')]
        registry.replace_owner('fixture', definitions)
        calls = []

        class Provider:
            async def call_tool(self, tool, arguments, session_id):
                calls.append((session_id, tool))
                return {'text': 'returned by fixture'}

        router = ToolRouter('', '', '', Mock(), Mock(), registry)
        self.addAsyncCleanup(router._client.aclose)
        router.bind_provider('fixture', Provider())
        session = ResearchSession(router, conversation_id='parent', model='fixture', allowed=True)
        session.record = {'workspace_capabilities': True, 'research_contract_version': 3}
        router.research = session
        await router.dispatch('fixture_write', {}, 'parent')
        roles = load_role_specs(Path('plugins'), registry.declared_tools)
        agent = SimpleNamespace(registry=registry, router=router, turn=session)
        executor = AgentSubagentExecutor(agent)
        for name in ('finance_analysis', 'finance_review'):
            task = executor._effective_task(RoleTask(roles[name], 'check', tool_scope=frozenset({'fixture_read'})))
            child = copy(router)
            child._registry = ScopedRegistry(registry, task.role.tools)
            result = await child.dispatch('fixture_read', {}, name)
            self.assertEqual(result['text'], 'returned by fixture')
            with self.assertRaises(ValueError):
                await child.dispatch('fixture_write', {}, name)
        self.assertEqual(calls, [('parent','fixture_write'), ('finance_analysis','fixture_read'),
                                 ('finance_review','fixture_read')])
        registry.unregister_owner('fixture')
        with self.assertRaises((PermissionError, ValueError)):
            await child.dispatch('fixture_read', {}, 'finance_review')

    def test_author_and_review_role_receive_registered_reader_and_lose_it_on_disable(self):
        registry = ToolRegistry(config.TOOLS_DIR,enabled_backends=('research-runtime',))
        registry.replace_owner('fixture',[{'name':'read_original','cloud':'read','backend':'fixture'}])
        research = ResearchSession(SimpleNamespace(known_tools=registry.known_tools,
            tool_tier_for_cloud=registry.get_cloud_tier,tools_for_role=registry.tools_for_role),
            conversation_id='c',model='frozen',allowed=True)
        research.record={'workspace_capabilities':True,'research_contract_version':3}
        agent = SimpleNamespace(registry=registry,router=SimpleNamespace(research=research),
            llm=SimpleNamespace(cloud=True),workspace_cloud_allowed=True,cloud_write_allowed=False)
        executor=AgentSubagentExecutor(agent)
        task=RoleTask(RoleSpec('review','inspect',inherit_tools=True),'open original')
        self.assertIn('read_original',research.tool_names)
        self.assertIn('read_original',executor._effective_task(task).role.tools)
        self.assertNotIn('shell_exec',executor._effective_task(task).role.tools)
        agent.workspace_cloud_allowed=False
        self.assertFalse(executor._effective_task(task).role.tools)
        agent.workspace_cloud_allowed=True
        registry.unregister_owner('fixture')
        self.assertNotIn('read_original',executor._effective_task(task).role.tools)

    def test_task_only_narrows_and_no_implicit_dependencies(self):
        roles = load_role_specs(Path('plugins'),ToolRegistry(config.TOOLS_DIR).declared_tools)
        tasks=role_tasks([{'role_id':'finance_analysis','task':'check source','tool_scope':['file_read']}],roles)
        self.assertEqual(tasks[0].role.depends_on,())
        self.assertEqual(tasks[0].tool_scope,{'file_read'})
