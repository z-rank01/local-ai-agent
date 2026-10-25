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

    async def test_report_projection_and_native_observer_share_version(self):
        import copy
        import hashlib
        import json
        from tests.test_stock_report_context import report
        from plugins.finance.report_context import MATERIAL_VIEW_VERSION
        observed, received, saved = [], [], {}
        class Model:
            cloud = False
            observes_native_requests = True
            async def chat_stream_with_tools(self, messages, tools, **kwargs):
                received.extend(copy.deepcopy(messages))
                kwargs['request_observer']('native_model_request', {'parameters': {'enable_thinking': True}})
                yield '', {'role': 'assistant', 'content': '现有材料为部分成果。'}
        agent = self.agent(Model(), SimpleNamespace(), [])
        def archive(row):
            eid = hashlib.sha256(json.dumps(row, sort_keys=True).encode()).hexdigest()
            saved[eid] = copy.deepcopy(row)
            return eid
        agent.context_mgr.archive = archive
        agent.context_mgr.reader = lambda eid: saved[eid]
        agent.context_mgr.evidence_ids = set()
        agent.audit.record = lambda event, data: observed.append((event, data))
        rows = report('a'*64, 'r', '旧阶段材料', '新版部分正文') + [{'role':'user','content':'解释 '+ 'a'*64}]
        original = copy.deepcopy(rows)
        events = [e async for e in agent.run(rows)]
        self.assertEqual(events[-1].kind, 'done')
        self.assertEqual(rows, original)
        native = next(data for event, data in observed if event == 'native_model_request')
        projected = next(json.loads(m['content'])['model_observation'] for m in received if m.get('tool_name') == 'stock_report_read')
        self.assertEqual(native['view_version'], MATERIAL_VIEW_VERSION)
        self.assertEqual(projected['material_view_version'], native['view_version'])
        self.assertTrue(saved)
