import unittest
from types import SimpleNamespace
from unittest.mock import patch
from core.agent import Agent
from core import config


class ResearchLoopTests(unittest.IsolatedAsyncioTestCase):
    async def exercise(self, repeated=False):
        observed=[]
        class Model:
            cloud=False
            async def chat_stream_with_tools(self,messages,tools):
                observed.append(tools)
                if tools and len(observed)<=5:
                    yield '',{'role':'assistant','content':'','tool_calls':[{'id':'s'+str(len(observed)),
                        'function':{'name':'web_search','arguments':'{"query":"公开研究"}'}}]}
                else:
                    yield '',{'role':'assistant','content':'研究成果与缺口'}
        class Router:
            async def dispatch(self,*args):
                return {'results':['同一证据' if repeated else '新增公开证据'+str(len(observed))]}
        async def process(messages):return messages
        agent=Agent(llm=Model(),router=Router(),registry=SimpleNamespace(get_definitions=lambda **kw:[
            {'type':'function','function':{'name':'web_search'}}]),
            audit=SimpleNamespace(record=lambda *a:None),context_mgr=SimpleNamespace(process=process),
            prompt_builder=SimpleNamespace(build=lambda **kw:'test'),max_rounds=2)
        agent.quality_first_research=True
        with patch.object(config,'WEB_SEARCH_BUDGET',1):
            events=[event async for event in agent.run([{'role':'user','content':'核查公开研究问题'}])]
        return events,observed

    async def test_batch_hint_does_not_stop_progress_or_ask_for_continue(self):
        events,observed=await self.exercise()
        self.assertEqual(len([e for e in events if e.kind=='tool_end']),5)
        self.assertEqual(events[-1].kind,'done')

    async def test_two_duplicate_rounds_stop_with_partial_delivery(self):
        events,observed=await self.exercise(repeated=True)
        self.assertEqual(len([e for e in events if e.kind=='tool_end']),3)
        self.assertIsNone(observed[-1])
        self.assertEqual(events[-1].kind,'done')

    async def handoff(self, submission, arguments, first_submit_code=None):
        offered, dispatched = [], []
        class Model:
            cloud = False
            async def chat_stream_with_tools(self, messages, tools):
                offered.append([t['function']['name'] for t in tools or []])
                number = len(offered)
                if number <= 2:
                    name = ('stock_research_submit' if first_submit_code else 'stock_report_read') if number == 1 else submission
                    args = {} if number == 1 else arguments
                    yield '', {'role': 'assistant', 'content': '', 'tool_calls': [
                        {'id': str(number), 'function': {'name': name, 'arguments': args}}]}
                else:
                    yield '', {'role': 'assistant', 'content': '交付已有成果及后台受理状态'}
        class Router:
            async def dispatch(self, name, params, session):
                dispatched.append((name, params))
                if len(dispatched) == 1 and first_submit_code:
                    return {'status':'TOOL_RETURNED', 'code':first_submit_code, 'broker_continue':False}
                return {'status': 'TIME_LIMIT'} if name == 'stock_report_read' else {'status': 'QUEUED', 'reference': 'a'*64}
        async def process(messages): return messages
        agent = Agent(llm=Model(), router=Router(), registry=SimpleNamespace(get_definitions=lambda **kw:[
            {'type': 'function', 'function': {'name': name}} for name in (
                'stock_report_read', 'stock_theme_research_submit', 'stock_research_submit', 'file_write')]),
            audit=SimpleNamespace(record=lambda *a:None), context_mgr=SimpleNamespace(process=process),
            prompt_builder=SimpleNamespace(build=lambda **kw:'test'), max_rounds=5)
        agent.quality_first_research = agent.stock_loop_enabled = True
        events = [e async for e in agent.run([{'role':'user','content':'继续研究公开政策的公司关联与反证'}])]
        return offered, dispatched, events

    async def test_expired_stock_window_hands_off_then_finalizes(self):
        offered, calls, events = await self.handoff('stock_theme_research_submit',
            {'reference':'a'*64, 'research_question':'核验政策公司关联与反证'})
        self.assertEqual(set(offered[1]), {'stock_theme_research_submit', 'stock_research_submit'})
        self.assertEqual([name for name, _ in calls], ['stock_report_read', 'stock_theme_research_submit'])
        self.assertEqual(offered[2], [])
        self.assertEqual(events[-1].kind, 'done')

    async def test_handoff_cannot_execute_write_redo_or_drop_question(self):
        for name, params in [('file_write', {'path':'x','content':'x'}),
                ('stock_research_submit', {'symbol':'600000','research_mode':'redo'}),
                ('stock_theme_research_submit', {'reference':'a'*64})]:
            with self.subTest(name=name):
                _, calls, _ = await self.handoff(name, params)
                self.assertEqual([n for n, _ in calls], ['stock_report_read'])

    async def test_only_actual_acceptance_suppresses_later_handoff(self):
        for code, expected in [('TASK_SUBMITTED', 1), ('NEEDS_REFERENCE', 2)]:
            with self.subTest(code=code):
                _, calls, _ = await self.handoff('stock_theme_research_submit',
                    {'reference':'a'*64, 'research_question':'核对政策传导'}, first_submit_code=code)
                self.assertEqual(len(calls), expected)
