import unittest
from unittest.mock import patch
from core import config
from core.agent import unfinished_execution
from tests import test_in1

DRAFT = {'role':'assistant','content':'让我先读取数据：\n```python\nprint(42)\n```'}

class RecoveryTests(unittest.IsolatedAsyncioTestCase):
    agent = test_in1.AgentTests.agent

    async def test_draft_then_real_tool_then_answer(self):
        agent=self.agent([DRAFT, {'role':'assistant','content':'','tool_calls':[test_in1.CALL]}, {'role':'assistant','content':'结果为42'}], {'stdout':'42'}, max_rounds=4)
        with patch.object(config,'WORKSPACE_CLOUD_ALLOWED',True): events=[e async for e in agent.run([{'role':'user','content':'请再试试'}])]
        self.assertEqual(self.runs,1)
        self.assertEqual(events[-1].kind,'done')
        self.assertEqual(events[-1].text,'结果为42')
        self.assertIn('执行检查',self.inputs[1][-1]['content'])

    async def test_repeated_drafts_are_bounded_and_not_executed(self):
        agent=self.agent([DRAFT.copy() for _ in range(3)], max_rounds=8)
        with patch.object(config,'WORKSPACE_CLOUD_ALLOWED',True): events=[e async for e in agent.run([{'role':'user','content':'运行计算'}])]
        self.assertEqual(len(self.inputs),3)
        self.assertEqual(self.runs,0)
        self.assertEqual(events[-1].kind,'error')
        self.assertIn('任务尚未完成',events[-1].text)

    async def test_explicit_example_is_not_retried(self):
        agent=self.agent([DRAFT])
        with patch.object(config,'WORKSPACE_CLOUD_ALLOWED',True): events=[e async for e in agent.run([{'role':'user','content':'仅给代码示例，不要执行'}])]
        self.assertEqual(len(self.inputs),1)
        self.assertEqual(self.runs,0)
        self.assertEqual(events[-1].kind,'done')

    def test_finished_answer_and_documented_limit_are_not_retried(self):
        self.assertFalse(unfinished_execution('已完成计算，复现代码：\n```python\nprint(42)\n```','计算'))
        self.assertFalse(unfinished_execution('无法执行，以下仅为示例：\n```python\nprint(42)\n```','计算'))


class ToolWaitTests(unittest.IsolatedAsyncioTestCase):
    async def test_waiting_tool_emits_heartbeat_then_result(self):
        import asyncio
        from types import SimpleNamespace
        from core.tool_router import ToolRouter
        async def dispatch(*args):
            await asyncio.sleep(2.1)
            return {'exit_code':0}
        packets=[p async for p in ToolRouter.dispatch_stream(SimpleNamespace(dispatch=dispatch),'pip_install',{})]
        self.assertEqual(packets[0]['event'],'heartbeat')
        self.assertTrue(any(p.get('elapsed',0)>=2 for p in packets))
        self.assertEqual(packets[-1],{'event':'result','result':{'exit_code':0}})

    async def test_closing_wait_cancels_local_request(self):
        import asyncio
        from types import SimpleNamespace
        from core.tool_router import ToolRouter
        cancelled=asyncio.Event()
        async def dispatch(*args):
            try: await asyncio.sleep(100)
            finally: cancelled.set()
        stream=ToolRouter.dispatch_stream(SimpleNamespace(dispatch=dispatch),'pip_install',{})
        await anext(stream)
        await asyncio.sleep(0)
        await stream.aclose()
        self.assertTrue(cancelled.is_set())
