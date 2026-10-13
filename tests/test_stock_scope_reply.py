import copy
import json
import unittest

from core.stock_source_coverage import scope_request_error, scope_reply
from tests.test_stock_source_coverage import REF, EVENT, URL, page
from tests import test_stock_source_quotes as quote_tests


def params(**extra):
    return {'reference': REF, 'event_id': EVENT, 'reply_focus': 'source_scope',
            'reply_source_url': URL, **extra}


class ScopeReplyTests(unittest.TestCase):
    def test_document_complete_does_not_borrow_other_task_eof(self):
        old=page((1,2), ref='c'*64);old['next_cursor']=''
        current=page((1,2));before=copy.deepcopy(current)
        reply=scope_reply(current,params(),REF,[old])
        self.assertIn('原文已全部收到',reply)
        self.assertIn('仍有后续页',reply)
        self.assertIn('| 当前保存 | 2 |',reply)
        self.assertIn('不能证明网站或 PDF 已完整采集',reply)
        self.assertNotIn(REF,reply);self.assertNotIn('legacy',reply)
        self.assertEqual(current,before)

    def test_partial_or_conflicting_snapshot_remains_visible(self):
        current=page((1,));other=page((2,),ref='c'*64)
        self.assertIn('仅收到一部分',scope_reply(current,params(),REF,[other]))
        changed=page((1,2));changed['source_coverage'][0]['snapshot_hash']='f'*64
        self.assertIn('暂不能确认已读完',scope_reply(changed,params(),REF,[]))

    def test_only_actual_bound_url_can_be_presented(self):
        value=page((1,2))
        self.assertIsNone(scope_reply(value,params(reply_source_url=URL+'x'),REF,[]))
        self.assertIsNone(scope_reply(value,params(), 'd'*64, []))
        value.pop('source_coverage')
        self.assertIsNone(scope_reply(value,params(),REF,[]))

    def test_analysis_and_legacy_calls_keep_old_flow(self):
        for p in ({'reference':REF,'event_id':EVENT}, {'reply_focus':'analysis'}):
            self.assertIsNone(scope_reply(page(),p,REF,[]))
            self.assertIsNone(scope_request_error(p,REF))
        self.assertIsNotNone(scope_request_error(params(reply_focus='other'),REF))
        self.assertIsNotNone(scope_request_error(params(reply_source_url=URL+'\ntext'),REF))


class ScopeReplyLoopTests(unittest.IsolatedAsyncioTestCase):
    agent=quote_tests.SourceQuoteLoopTests.agent

    async def test_selected_verified_scope_finishes_without_second_cloud_answer(self):
        calls=[]
        class Model:
            cloud=False
            async def chat_stream_with_tools(self,messages,tools,**kwargs):
                calls.append(1)
                if len(calls)>1:raise AssertionError('unexpected extra generation')
                yield '',{'role':'assistant','content':'','tool_calls':[{'id':'read','function':{
                    'name':'stock_public_evidence_read','arguments':params()}}]}
        class Router:
            async def dispatch(self,*args):return {'model_observation':page((1,2))}
        history=[{'role':'user','content':'核对来源范围 '+REF}]
        events=[e async for e in self.agent(Model(),Router(),['stock_public_evidence_read']).run(history)]
        self.assertEqual(len(calls),1)
        self.assertTrue(any(e.kind=='tool_end' for e in events))
        self.assertIn('原文已全部收到',next(e.text for e in events if e.kind=='done'))
        self.assertTrue(any(e.kind=='message' and e.data['message'].get('role')=='tool' for e in events))

    async def test_analysis_and_unverified_scope_continue_model_semantics(self):
        for selected,missing in (({},False),(params(),True)):
            calls=[]
            class Model:
                cloud=False
                async def chat_stream_with_tools(self,messages,tools,**kwargs):
                    calls.append(1)
                    if len(calls)==1:
                        yield '',{'role':'assistant','content':'','tool_calls':[{'id':'read','function':{
                            'name':'stock_public_evidence_read','arguments':selected}}]}
                    else:yield '',{'role':'assistant','content':'当前资料不足以判断需求兑现。'}
            class Router:
                async def dispatch(self,*args):
                    value=page((1,2))
                    if missing:value.pop('source_coverage')
                    return value
            events=[e async for e in self.agent(Model(),Router(),['stock_public_evidence_read']).run(
                [{'role':'user','content':REF}])]
            self.assertEqual(len(calls),2)
            self.assertEqual(next(e.text for e in events if e.kind=='done'),'当前资料不足以判断需求兑现。')

    async def test_bridge_rejects_wrong_ref_before_broker_and_never_forwards_presentation(self):
        import httpx
        from core.stock_bridge import StockBridge
        from tests.test_stock_bridge import make_broker,ok_step
        captured=[]
        broker=make_broker(captured,step_responses=[ok_step(page((1,2)))])
        client=httpx.AsyncClient(transport=httpx.MockTransport(broker));self.addAsyncCleanup(client.aclose)
        bridge=StockBridge('http://paper.test','token',client=client)
        bridge.set_turn('s','turn',REF,allow_report_body=True)
        failed=await bridge.call_tool('stock_public_evidence_read',params(reference='c'*64),'s')
        self.assertIn('未执行',failed['error']);self.assertEqual(captured,[])
        value=await bridge.call_tool('stock_public_evidence_read',params(),'s')
        self.assertIn('model_observation',value)
        wire=json.dumps(captured)
        self.assertNotIn('reply_focus',wire);self.assertNotIn('reply_source_url',wire)
        bridge.set_turn('private','turn2',REF,allow_report_body=False)
        count=len(captured)
        self.assertIn('error',await bridge.call_tool('stock_public_evidence_read',params(),'private'))
        self.assertEqual(len(captured),count)
