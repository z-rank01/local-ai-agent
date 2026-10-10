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
