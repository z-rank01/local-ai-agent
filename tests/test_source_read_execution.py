import json
import unittest
from unittest.mock import patch

from tests import test_in1, test_stock_source_quotes
from plugins.finance.source_quotes import source_task_index


class SourceIndexTests(unittest.TestCase):
    def test_legacy_receipt_pairing_ignores_stage_parent_and_failed_source(self):
        old, current = 'a'*64, 'b'*64
        def call(cid, token, name='stock_report_read'):
            return {'role':'assistant','tool_calls':[{'id':cid,'function':{'name':name,'arguments':{'reference':token}}}]}
        def result(cid, name, value):
            return {'role':'tool','tool_call_id':cid,'tool_name':name,'content':json.dumps(value)}
        history=[call('old',old),result('old','stock_report_read',{'kind':'local_report_page','body':'old'}),
                 call('current',current),result('current','stock_report_read',{'kind':'local_report_page','body':'stage parent '+old}),
                 call('find-old',old,'stock_public_evidence_find'),result('find-old','stock_public_evidence_find',{'kind':'public_evidence_find','items':[]}),
                 call('bad',old,'stock_public_evidence_read'),result('bad','stock_public_evidence_read',{'error':'RULE_BLOCKED'}),
                 {'role':'user','content':json.dumps({'source_reference':'c'*64})}]
        before=json.dumps(history)
        index=source_task_index(history)
        self.assertEqual(index[0]['source_reference'],current)
        self.assertEqual(len(index),2)
        self.assertEqual(json.dumps(history),before)


class SourceChoiceProviderTests(unittest.IsolatedAsyncioTestCase):
    async def client(self,chunks,status=200):
        return await test_in1.ProviderTests.client(self,chunks,status)
    def configure(self,client):
        client.spec.update(source_read_tool_choice={'mode':'named_non_thinking'},options={'enable_thinking':False,'temperature':0.3})
        return [{'type':'function','function':{'name':'stock_public_evidence_find'}}]

    async def test_actual_named_payload_and_no_persistent_override_or_body_log(self):
        client=await self.client([{'id':'response-id','choices':[{'delta':{'tool_calls':[{'index':0,'id':'call','function':{'name':'stock_public_evidence_find','arguments':'{}'}}]},'finish_reason':'tool_calls'}]}])
        tools=self.configure(client);observed=[]
        with patch.dict('os.environ',{'IN1_FAKE_KEY':'dummy-secret'}):
            _=[e async for e in client.chat_stream_with_tools([{'role':'user','content':'private-test-body'}],tools,
                selected_source_tool='stock_public_evidence_find',request_observer=lambda event,data:observed.append((event,data)))]
            self.assertEqual(self.payload['tool_choice'],{'type':'function','function':{'name':'stock_public_evidence_find'}})
            self.assertEqual(self.payload['temperature'],0.3)
            self.assertFalse(self.payload['enable_thinking'])
            self.assertNotIn('private-test-body',json.dumps(observed))
            self.assertNotIn('dummy-secret',json.dumps(observed))
            _=[e async for e in client.chat_stream_with_tools([],tools)]
            self.assertNotIn('tool_choice',self.payload)
        self.assertNotIn('tool_choice',client.spec['options'])

    async def test_thinking_or_unoffered_tool_blocks_without_silent_change(self):
        client=await self.client([]);tools=self.configure(client)
        client.spec['options']['enable_thinking']=True
        with self.assertRaisesRegex(RuntimeError,'不支持'):
            _=[e async for e in client.chat_stream_with_tools([],tools,selected_source_tool='stock_public_evidence_find')]
        self.assertTrue(client.spec['options']['enable_thinking'])
        client.spec['options']['enable_thinking']=False
        with self.assertRaisesRegex(RuntimeError,'不支持'):
            _=[e async for e in client.chat_stream_with_tools([],tools,selected_source_tool='stock_research_submit')]
        self.assertFalse(hasattr(self,'payload'))

    async def test_supplier_ignoring_named_choice_fails_without_retry(self):
        client=await self.client([{'choices':[{'delta':{'content':'no tool'},'finish_reason':'stop'}]}])
        tools=self.configure(client);observed=[]
        with patch.dict('os.environ',{'IN1_FAKE_KEY':'dummy'}),self.assertRaisesRegex(RuntimeError,'未返回指定'):
            _=[e async for e in client.chat_stream_with_tools([],tools,selected_source_tool='stock_public_evidence_find',
                request_observer=lambda *args:observed.append(args))]
        self.assertEqual(sum(event=='native_model_request' for event,_ in observed),1)
        terminal=[data for event,data in observed if event=='native_model_result']
        self.assertEqual(len(terminal),1)
        self.assertEqual(terminal[0]['result_status'],'COMPLETE')
        self.assertEqual(terminal[0]['delivery_status'],'FAILED')
        self.assertEqual(terminal[0]['failure_category'],'TOOL_CHOICE_NOT_HONOURED')


class SourceReadExecutionTests(unittest.IsolatedAsyncioTestCase):
    async def test_no_continuation_call_after_raw_read_returns_to_checked_delivery(self):
        contexts=[];reads=[]
        class Model:
            cloud=False
            supports_source_read_choice=False
            async def chat_stream_with_tools(self,messages,tools,**kw):
                contexts.append(messages)
                step=len(contexts)
                if step==2:
                    yield '',{'role':'assistant','content':'','tool_calls':[{'id':'read','function':{
                        'name':'stock_public_evidence_read','arguments':{'reference':'a'*64,'event_id':'b'*16}}}]}
                    return
                text='原文：“虚构原句”。' if step==1 else '定位步骤草稿不能作为原文' if step==3 else '原文：“未用完的中央额度收回”。仍未读完全部原文。'
                yield text,None;yield '',{'role':'assistant','content':text}
        class Router:
            async def dispatch(self,name,params,session):
                reads.append(params)
                return {'kind':'public_evidence_read','job_id':'job','event_id':'b'*16,'total_excerpts':2,
                    'excerpts':[{'kind':'ORIGINAL_TEXT','excerpt_id':'first','text':'未用完的中央额度收回。'}],
                    'next_cursor':'tail'}
        agent=test_stock_source_quotes.SourceQuoteLoopTests.agent(self,Model(),Router(),['stock_public_evidence_find','stock_public_evidence_read'])
        events=[e async for e in agent.run(test_stock_source_quotes.SourceQuoteLoopTests.history(self))]
        self.assertEqual(len(reads),1)
        self.assertEqual(len(contexts),4)
        self.assertTrue(any('尚未完整读到末尾' in m.get('content','') for m in contexts[-1]))
        self.assertEqual(events[-1].kind,'done')
        self.assertIn('仍未读完',events[-1].text)
        self.assertNotIn('定位步骤草稿',''.join(e.text for e in events if e.kind=='token'))

    async def test_missed_query_keeps_focus_then_model_lists_sources_and_reads(self):
        contexts, dispatched = [], []
        class Model:
            cloud=False
            supports_source_read_choice=False
            async def chat_stream_with_tools(self,messages,tools,**kw):
                contexts.append(messages)
                step=len(contexts)
                if step in (1,5):
                    text='原文：“虚构的原句”。' if step==1 else '原文：“未用完的中央额度收回”。'
                    yield text,None;yield '',{'role':'assistant','content':text}
                    return
                if step==3:
                    assert not any('旧错误答案' in m.get('content','') for m in messages)
                    assert '省略search列出' in messages[0]['content']
                    assert any('"total": 0' in m.get('content','') for m in messages if m.get('role')=='tool')
                name='stock_public_evidence_read' if step==4 else 'stock_public_evidence_find'
                args={'reference':'a'*64}
                if step==2: args['search']='问题改写的组合词'
                if step==4: args['event_id']='b'*16
                yield '',{'role':'assistant','content':'','tool_calls':[{'id':str(step),'function':{'name':name,'arguments':args}}]}
        class Router:
            async def dispatch(self,name,params,session):
                dispatched.append((name,params))
                if name.endswith('find'):
                    return {'kind':'public_evidence_find','total':0,'items':[]} if params.get('search') else {
                        'kind':'public_evidence_find','total':1,'items':[{'event_id':'b'*16,'title':'资金管理通知'}]}
                return {'kind':'public_evidence_read','excerpts':[{'kind':'ORIGINAL_TEXT','text':'未用完的中央额度收回。'}]}
        agent=test_stock_source_quotes.SourceQuoteLoopTests.agent(self,Model(),Router(),['stock_public_evidence_find','stock_public_evidence_read'])
        history=test_stock_source_quotes.SourceQuoteLoopTests.history(self)
        history.insert(1,{'role':'assistant','content':'旧错误答案'})
        events=[e async for e in agent.run(history)]
        self.assertEqual(dispatched[0][1]['search'],'问题改写的组合词')
        self.assertNotIn('search',dispatched[1][1])
        self.assertEqual(len(dispatched),3)
        self.assertEqual(events[-1].kind,'done')
        self.assertIn('未用完的中央额度收回',events[-1].text)
        self.assertTrue(any('旧错误答案' in m.get('content','') for m in contexts[-1]))

    async def test_source_continuation_keeps_late_counterevidence_before_answer(self):
        reads=[];rounds=[]
        class Model:
            cloud=False
            supports_source_read_choice=False
            async def chat_stream_with_tools(self,messages,tools,**kw):
                rounds.append(1)
                if len(rounds)==1:
                    text='原文：“保证新增收入”。'
                    yield text,None;yield '',{'role':'assistant','content':text}
                elif len(rounds)==2:
                    yield '',{'role':'assistant','content':'','tool_calls':[{'id':'lookup','function':{
                        'name':'stock_public_evidence_find','arguments':{'reference':'a'*64,'search':'合同'}}}]}
                elif len(reads)<2:
                    cursor='' if not reads else 'tail'
                    yield '',{'role':'assistant','content':'','tool_calls':[{'id':'read'+str(len(reads)),'function':{
                        'name':'stock_public_evidence_read','arguments':{'reference':'a'*64,'event_id':'b'*16,'cursor':cursor}}}]}
                else:
                    text='原文：“合同已经终止”。不能把合同总额计为新增收入。'
                    yield text,None;yield '',{'role':'assistant','content':text}
        class Router:
            async def dispatch(self,name,params,session):
                if name.endswith('find'):
                    return {'kind':'public_evidence_find','items':[{'event_id':'b'*16}]}
                reads.append(params['cursor'])
                return {'kind':'public_evidence_read','excerpts':[{'kind':'ORIGINAL_TEXT','text':
                    '原合同金额一亿元。' if len(reads)==1 else '合同已经终止，不应计为新增收入。'}],
                    'next_cursor':'tail' if len(reads)==1 else ''}
        agent=test_stock_source_quotes.SourceQuoteLoopTests.agent(self,Model(),Router(),['stock_public_evidence_find','stock_public_evidence_read'])
        events=[e async for e in agent.run(test_stock_source_quotes.SourceQuoteLoopTests.history(self))]
        self.assertEqual(reads,['','tail'])
        self.assertEqual(events[-1].kind,'done')
        self.assertIn('合同已经终止',events[-1].text)

    async def test_parameter_step_without_call_is_not_published_as_completed_research(self):
        rounds=[]
        class Model:
            cloud=False
            supports_source_read_choice=False
            async def chat_stream_with_tools(self,*args):
                rounds.append(1)
                text='原文：“无来源引文”。' if len(rounds)==1 else '本次补查未执行的原始回答'
                yield text,None;yield '',{'role':'assistant','content':text}
        agent=test_stock_source_quotes.SourceQuoteLoopTests.agent(self,Model(),object(),['stock_public_evidence_find','stock_public_evidence_read'])
        events=[e async for e in agent.run(test_stock_source_quotes.SourceQuoteLoopTests.history(self))]
        execution=[e for e in events if e.kind=='quality_rejected' and e.data.get('quality_status')=='rejected_source_execution']
        self.assertEqual(execution[0].data['rejected_answer'],'本次补查未执行的原始回答')
        self.assertEqual(events[-1].kind,'error')
        self.assertIn('未将待核草稿作为研究结论交付', events[-1].text)
        self.assertEqual(len(rounds),2)
        self.assertNotIn('本次补查未执行的原始回答',''.join(e.text for e in events if e.kind=='token'))

    async def test_thinking_model_uses_focused_auto_call_without_turning_thinking_off(self):
        contexts=[];names=[]
        class Model:
            cloud=False
            supports_source_read_choice=False
            observes_native_requests=True
            async def chat_stream_with_tools(self,messages,tools,**kw):
                contexts.append(messages)
                assert 'selected_source_tool' not in kw
                if len(contexts)==1:
                    text='原文：“无法核验的引文”。'
                    yield text,None;yield '',{'role':'assistant','content':text}
                elif len(contexts)==2:
                    assert 'request_observer' in kw
                    assert not any('旧历史错误' in m.get('content','') for m in messages)
                    yield '',{'role':'assistant','content':'','tool_calls':[{'id':'read','function':{
                        'name':'stock_public_evidence_read','arguments':{'reference':'a'*64,'event_id':'b'*16}}}]}
                else:
                    text='原文：“资金收回中央”。'
                    yield text,None;yield '',{'role':'assistant','content':text}
        class Router:
            async def dispatch(self,name,params,session):
                names.append(name)
                return {'kind':'public_evidence_read','excerpts':[{'kind':'ORIGINAL_TEXT','text':'资金收回中央。'}]}
        agent=test_stock_source_quotes.SourceQuoteLoopTests.agent(self,Model(),Router(),['stock_public_evidence_find','stock_public_evidence_read'])
        history=test_stock_source_quotes.SourceQuoteLoopTests.history(self);history.insert(1,{'role':'assistant','content':'旧历史错误'})
        events=[e async for e in agent.run(history)]
        self.assertEqual(names,['stock_public_evidence_read'])
        self.assertIn('资金收回中央',events[-1].text)
        self.assertTrue(any('旧历史错误' in m.get('content','') for m in contexts[-1]))

    async def test_failed_quote_requires_lookup_read_then_releases_choice(self):
        selected=[];dispatched=[]
        class Model:
            cloud=False
            supports_source_read_choice=True
            async def chat_stream_with_tools(self,messages,tools,**kw):
                name=kw.get('selected_source_tool');selected.append(name)
                if len(selected)==1:
                    text='原文：“无原文支持的断言”。'
                    yield text,None
                    yield '',{'role':'assistant','content':text}
                elif name:
                    assert not any('旧错误回答应当被当作事实' in m.get('content','') for m in messages)
                    assert any(m.get('role') == 'user' and '核对资金' in m.get('content','') for m in messages)
                    self_names=[d['function']['name'] for d in tools]
                    assert 'stock_research_submit' not in self_names
                    yield '内部补查参数生成，不是报告正文',None
                    yield '',{'role':'assistant','content':'','tool_calls':[{'id':str(len(selected)),'function':{'name':name,'arguments':{'reference':'b'*64,**({'event_id':'c'*16} if name.endswith('read') else {'search':'未用完'})}}}]}
                else:
                    text='原文：“未用完的中央额度收回”。'
                    yield text,None
                    yield '',{'role':'assistant','content':text}
        class Router:
            async def dispatch(self,name,params,session):
                dispatched.append((name,params))
                if name.endswith('find'):
                    return {'kind':'public_evidence_find','items':[{'event_id':'c'*16,'source_reference':'b'*64,'read_cursor':'tail'}]}
                return {'kind':'public_evidence_read','excerpts':[{'kind':'ORIGINAL_TEXT','text':'未用完的中央额度收回。'}]}
        agent=test_stock_source_quotes.SourceQuoteLoopTests.agent(self,Model(),Router(),['stock_public_evidence_find','stock_public_evidence_read','stock_research_submit'])
        history=test_stock_source_quotes.SourceQuoteLoopTests.history(self)
        history.insert(1,{'role':'assistant','content':'旧错误回答应当被当作事实'})
        events=[e async for e in agent.run(history)]
        self.assertEqual(selected,[None,'stock_public_evidence_find','stock_public_evidence_read',None])
        self.assertEqual(len(dispatched),2)
        self.assertEqual(events[-1].kind,'done')
        self.assertIn('未用完的中央额度收回',events[-1].text)
        self.assertNotIn('内部补查参数生成',''.join(e.text for e in events if e.kind=='token'))
