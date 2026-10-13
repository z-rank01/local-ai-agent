import unittest
from types import SimpleNamespace
from tests import test_stock_source_quotes

class NativeObservationTests(unittest.IsolatedAsyncioTestCase):
    agent=test_stock_source_quotes.SourceQuoteLoopTests.agent
    async def test_request_observed_without_archived_explanation_view(self):
        observed=[]
        class Model:
            cloud=False
            observes_native_requests=True
            async def chat_stream_with_tools(self,messages,tools,**kwargs):
                self_received=kwargs.get('request_observer')
                assert self_received is not None, 'plain final request missing observer'
                self_received('native_model_request',{'parameters':{'model':'same-model','enable_thinking':True,'stream':True},
                    'input_hash':'h','input_chars':123,'tool_names':[]})
                yield '已核对。',None;yield '',{'role':'assistant','content':'已核对。'}
        agent=self.agent(Model(),SimpleNamespace(),[])
        agent.audit.record=lambda event,data:observed.append((event,data))
        events=[e async for e in agent.run([{'role':'user','content':'说明现有结果，不执行操作。'}])]
        self.assertEqual(events[-1].kind,'done')
        native=[row for event,row in observed if event=='native_model_request']
        self.assertEqual(len(native),1);self.assertIsNone(native[0]['view_version'])
        self.assertTrue(native[0]['parameters']['enable_thinking']);self.assertNotIn('content',native[0])
