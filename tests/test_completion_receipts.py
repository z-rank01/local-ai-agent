"""Terminal summaries survive refresh; unsuccessful tool loops are not success."""
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from core.agent import Agent, AgentEvent
from bff.schemas import ChatRequest
from tests import test_in1
from harness.runtime.receipts import call_summary


class CompletionTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        await test_in1.ServiceTests.asyncSetUp(self)

    async def test_queued_receipt_stays_queued_in_events_and_saved_history(self):
        async def run(*args, **kwargs):
            result = {'id': 'example', 'state': 'QUEUED'}
            yield AgentEvent('tool_end', text=call_summary(result, 'ok'), data={
                'name': 'example_task', 'call_id': 'call', 'status': 'ok',
                'result': result, 'result_preview': 'QUEUED'})
            yield AgentEvent('done', text='任务已提交，尚未取得完成回执。')
        with patch.object(Agent, 'run', run):
            events = [e async for e in self.service.stream_chat(ChatRequest(message='处理材料', model='qwen:qwen3.5-flash'))]
        receipt = next(e for e in events if e.event == 'tool.completed')
        self.assertEqual(receipt.data['headline'], '任务已排队')
        saved = next(m for m in self.service.get_messages(events[0].conversation_id) if m.role == 'tool')
        self.assertIn('任务已排队', saved.content)
        self.assertNotIn('已完成', saved.content)

    async def test_done_only_summary_is_saved_once_and_message_done_is_not_duplicated(self):
        for with_message in (False, True):
            async def run(*args, **kwargs):
                if with_message:
                    yield AgentEvent('message', data={'message': {'role': 'assistant', 'content': '实际收尾说明'}})
                yield AgentEvent('done', text='实际收尾说明')
            with patch.object(Agent, 'run', run):
                events = [e async for e in self.service.stream_chat(ChatRequest(message='请处理材料', model='qwen:qwen3.5-flash'))]
            answers = [m.content for m in self.service.get_messages(events[0].conversation_id) if m.role == 'assistant']
            self.assertEqual(answers, ['实际收尾说明'])

    async def test_repeated_failures_and_ignored_finalization_have_explicit_error(self):
        class Model:
            cloud = False
            async def chat_stream_with_tools(self, messages, tools=None, **kwargs):
                yield '', {'role': 'assistant', 'content': '', 'tool_calls': [{
                    'id': 'attempt', 'function': {'name': 'example_task', 'arguments': '{}'}}]}
        class Router:
            async def dispatch(self, *args):
                return {'error': 'fixture: unavailable'}
        async def process(messages): return messages
        agent = Agent(llm=Model(), router=Router(), registry=SimpleNamespace(get_definitions=lambda **kw: [
            {'type': 'function', 'function': {'name': 'example_task'}}]),
            audit=SimpleNamespace(record=lambda *a: None), context_mgr=SimpleNamespace(process=process),
            prompt_builder=SimpleNamespace(build=lambda **kw: 'test'), max_rounds=10)
        agent.quality_first_research = True
        events = [e async for e in agent.run([{'role': 'user', 'content': '处理两份文件'}])]
        self.assertEqual(events[-1].kind, 'error')
        self.assertIn('未完成', events[-1].text)
        self.assertEqual(sum(e.kind == 'tool_end' for e in events), 2)
