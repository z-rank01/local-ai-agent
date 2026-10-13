import json
import unittest
from unittest.mock import patch

from tests import test_in1, test_stock_source_quotes
from core.stock_source_quotes import source_task_index


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
        self.assertEqual(len(observed),1)


class SourceReadExecutionTests(unittest.IsolatedAsyncioTestCase):
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
