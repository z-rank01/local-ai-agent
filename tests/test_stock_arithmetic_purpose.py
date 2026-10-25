import copy,json,unittest
from decimal import Decimal,localcontext
from plugins.finance.arithmetic_purpose import validated_service_mean,calculation_reply,request_error
from tests.test_stock_calculation_review import receipt,observation,REF
from tests import test_stock_bridge as bt,test_stock_source_quotes as qt


def value():
    v=observation();c=receipt()
    with localcontext() as ctx:
        ctx.prec=28
        c['annual_source']={'kind':'SOURCE_FIELD','metric':'revenue','unit':'CNY','symbol':'688178',
            'report_period':'20251231','value_yuan':'500764121.06','url':'https://vip.stock.finance.sina.com.cn/x',
            'location':'利润表/20251231/营业收入','event_id':'d'*16,'read_cursor':'20:'+('f'*16)}
        c['service_mean']={'version':'event-service-mean-v1','service_years':'3','mean_amount_yuan':'38000400',
            'mean_percent':str(Decimal('38000400')/Decimal('500764121.06')*100),
            'duration_source':{'kind':'ORIGINAL_TEXT','symbol':'688178','quote':'运营期限：3 年',
                'published':'2026-10-10','url':'https://pdf.dfcfw.com/a.pdf','location':'公告原文',
                'event_id':'d'*16,'read_cursor':'0:'+('a'*16)}}
    v['program_calculations']=[c];v['answer_pages'][0]['body']+='；运营期限：3 年，平均38000400元，7.59%'
    return v


class PurposeTests(unittest.TestCase):
    def test_history_keeps_base_scale_but_discards_invalid_optional_average(self):
        from plugins.finance.calculation_review import calculation_index
        v=value();v['program_calculations'][0]['service_mean']['mean_amount_yuan']='3800.04'
        messages=[{'role':'assistant','tool_calls':[{'id':'r','function':{'name':'stock_report_read','arguments':{'reference':REF}}}]},
                  {'role':'tool','tool_name':'stock_report_read','tool_call_id':'r','content':json.dumps(v)}]
        rows=calculation_index(messages)[REF]
        self.assertEqual(rows[0]['amount_yuan'],'114001200')
        self.assertNotIn('service_mean',rows[0])
        self.assertIn('service_mean',v['program_calculations'][0])

    def test_exact_public_calculation_without_uniform_or_income_prediction(self):
        v=value();before=copy.deepcopy(v)
        reply=calculation_reply(v,{'reference':REF,'reply_focus':'calculation'},REF)
        self.assertIn('11400.12万元',reply);self.assertIn('50076.412106万元',reply)
        self.assertIn('3800.04万元',reply);self.assertIn('不要求各年均匀',reply)
        self.assertIn('不代表每年实际',reply);self.assertIn('约7.59%',reply)
        self.assertNotIn(REF,reply);self.assertNotIn('法律',reply);self.assertEqual(v,before)

    def test_default_analysis_and_unbound_reference_keep_model_path(self):
        for p in ({'reference':REF},{'reference':REF,'reply_focus':'analysis'}):
            self.assertIsNone(calculation_reply(value(),p,REF))
        self.assertIsNotNone(request_error({'reference':'b'*64,'reply_focus':'calculation'},REF))
        self.assertIsNotNone(request_error({'reply_focus':'other'},REF))

    def test_bad_optional_mean_does_not_approve_reply(self):
        for key,bad in [('mean_amount_yuan','3800.04'),('service_years','0'),('mean_percent','7.6'),('version','other')]:
            v=value();v['program_calculations'][0]['service_mean'][key]=bad
            self.assertIsNone(calculation_reply(v,{'reference':REF,'reply_focus':'calculation'},REF))
        for key,bad in [('quote','期限3年'),('symbol','300070'),('published','bad'),('url','https://127.0.0.1/private')]:
            v=value();v['program_calculations'][0]['service_mean']['duration_source'][key]=bad
            self.assertIsNone(calculation_reply(v,{'reference':REF,'reply_focus':'calculation'},REF))

    def test_revision_body_missing_source_or_mean_cannot_finish(self):
        for alter in ('revision','body','source','mean','failed'):
            v=value()
            if alter=='revision':v['answer_pages'][0]['reader_revision']='f'*64
            elif alter=='body':v['answer_pages'][0]['body']='旧版正文'
            elif alter=='source':v['program_calculations'][0].pop('annual_source')
            elif alter=='mean':v['program_calculations'][0].pop('service_mean')
            else:v['error']='RULE_BLOCKED'
            self.assertIsNone(calculation_reply(v,{'reference':REF,'reply_focus':'calculation'},REF))


class PurposeLoopTests(unittest.IsolatedAsyncioTestCase):
    agent=qt.SourceQuoteLoopTests.agent
    async def test_explicit_math_selection_finishes_from_current_read_not_second_model(self):
        calls=[];audit=[]
        class Model:
            cloud=False
            async def chat_stream_with_tools(self,messages,tools,**kwargs):
                calls.append(1)
                if len(calls)>1:raise AssertionError('extra interpretation')
                yield '',{'role':'assistant','content':'','tool_calls':[{'id':'read','function':{
                    'name':'stock_report_read','arguments':{'reference':REF,'reply_focus':'calculation'}}}]}
        class Router:
            async def dispatch(self,*args):return {'model_observation':value()}
        a=self.agent(Model(),Router(),['stock_report_read']);a.audit.record=lambda event,data:audit.append((event,data))
        events=[e async for e in a.run([{'role':'user','content':REF}])]
        final=next(e.text for e in events if e.kind=='done')
        self.assertEqual(final,calculation_reply(value(),{'reference':REF,'reply_focus':'calculation'},REF))
        self.assertEqual(len(calls),1);self.assertTrue(any(e=='program_calculation_reply' for e,d in audit))

    async def test_analysis_or_missing_mean_preserves_semantic_path(self):
        for selected,missing in [('analysis',False),('calculation',True)]:
            calls=[]
            class Model:
                cloud=False
                async def chat_stream_with_tools(self,messages,tools,**kwargs):
                    calls.append(1)
                    if len(calls)==1:
                        yield '',{'role':'assistant','content':'','tool_calls':[{'id':'read','function':{
                            'name':'stock_report_read','arguments':{'reference':REF,'reply_focus':selected}}}]}
                    else:yield '',{'role':'assistant','content':'项目兑现仍需核对条件。'}
            class Router:
                async def dispatch(self,*args):
                    v=value()
                    if missing:v['program_calculations'][0].pop('service_mean')
                    return {'model_observation':v}
            events=[e async for e in self.agent(Model(),Router(),['stock_report_read']).run([{'role':'user','content':REF}])]
            self.assertEqual(len(calls),2);self.assertEqual(next(e.text for e in events if e.kind=='done'),'项目兑现仍需核对条件。')

    async def test_bridge_gates_authorization_drops_only_bad_mean_and_does_not_forward_purpose(self):
        import httpx
        from plugins.finance.bridge import StockBridge
        captured=[];v=value();v['program_calculations'][0]['service_mean']['service_years']='4'
        broker=bt.make_broker(captured,step_responses=[bt.ok_step(v)])
        client=httpx.AsyncClient(transport=httpx.MockTransport(broker));self.addAsyncCleanup(client.aclose)
        bridge=StockBridge('http://paper.test','token',client=client)
        params={'reference':REF,'reply_focus':'calculation'}
        bridge.set_turn('s','t',REF,allow_report_body=False)
        self.assertIn('error',await bridge.call_tool('stock_report_read',params,'s'));self.assertEqual(captured,[])
        bridge.set_turn('s','t',REF,allow_report_body=True)
        self.assertIn('error',await bridge.call_tool('stock_report_read',dict(params,reference='b'*64),'s'));self.assertEqual(captured,[])
        result=await bridge.call_tool('stock_report_read',params,'s')
        calc=result['model_observation']['program_calculations'][0]
        self.assertNotIn('service_mean',calc);self.assertEqual(calc['amount_yuan'],'114001200')
        self.assertNotIn('reply_focus',json.dumps(captured))
