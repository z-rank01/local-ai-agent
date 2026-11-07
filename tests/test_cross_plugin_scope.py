from pathlib import Path
from types import SimpleNamespace
import unittest

from core.subagent_executor import AgentSubagentExecutor
from core.tool_registry import ToolRegistry
from core import config
from harness.contracts.subagents import RoleSpec, RoleTask
from harness.runtime.subagents.spec import role_tasks, load_role_specs
from plugins.finance.research.session import ResearchSession


class CrossPluginScopeTests(unittest.TestCase):
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
