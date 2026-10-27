import unittest
from types import SimpleNamespace
from tests.test_stock_source_quotes import SourceQuoteLoopTests
from tests.test_stock_reply_purpose import history
from tests.test_stock_arithmetic_purpose import value,REF


class PurposeContextOrderTests(unittest.IsolatedAsyncioTestCase):
    async def test_pure_read_never_summarises_unused_full_history(self):
        calls=[]
        class Model:
            cloud=False
            async def chat_stream_with_tools(self,messages,tools,**kw):
                calls.append(messages)
                self_outer.assertEqual(len(messages),3)
                yield '',{'role':'assistant','content':'','tool_calls':[{'id':'r','function':{
                    'name':'stock_report_read','arguments':{'reference':REF,'reply_focus':'calculation'}}}]}
        class Router:
            async def dispatch(self,*args):return {'model_observation':value()}
        self_outer=self
        a=SourceQuoteLoopTests.agent(self,Model(),Router(),['stock_report_read'])
        a.turn_reply_purpose_enabled=True
        async def process(messages):raise AssertionError('pure purpose must not summarise unused history')
        a.context_mgr=SimpleNamespace(process=process)
        events=[e async for e in a.run(history())]
        self.assertEqual(len(calls),1);self.assertIn('不要求各年均匀',events[-1].text)

    async def test_analysis_processes_complete_history_after_semantic_selection(self):
        calls=[];processed=[]
        class Model:
            cloud=False
            async def chat_stream_with_tools(self,messages,tools,**kw):
                calls.append(messages)
                yield '',{'role':'assistant','content':'{"reply_focus":"analysis"}' if len(calls)==1 else '原分析继续。'}
        a=SourceQuoteLoopTests.agent(self,Model(),SimpleNamespace(),['stock_report_read'])
        a.turn_reply_purpose_enabled=True
        async def process(messages):
            self.assertEqual(len(calls),1)
            self.assertTrue(any('末尾反证' in (m.get('content') or '') for m in messages))
            processed.append(messages);return messages
        a.context_mgr=SimpleNamespace(process=process)
        events=[e async for e in a.run(history())]
        self.assertEqual(len(processed),1);self.assertEqual(events[-1].text,'原分析继续。')
