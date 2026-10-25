import copy
import json
import unittest
from types import SimpleNamespace

from plugins.finance.reply_purpose import selection_view, analysis_decision
from plugins.finance.task_source_view import lookup_index
from plugins.finance.source_quotes import source_task_index
from tests.test_stock_explanation_view import source_turn
from tests.test_stock_source_quotes import SourceQuoteLoopTests
from tests.test_stock_arithmetic_purpose import value, REF


def history(question=None):
    return [{'role':'system','content':'原方法与权限保持'}]+source_turn(REF,'旧')+[
        {'role':'user','content':question or ('核对当前资料 '+REF)}]


class PurposeViewTests(unittest.TestCase):
    def test_current_question_index_and_read_tools_only_without_mutating_history(self):
        rows=history();old=copy.deepcopy(rows)
        defs=[{'function':{'name':n}} for n in ('stock_report_read','stock_research_submit','stock_public_evidence_read')]
        selected,tools=selection_view(rows,rows[-1]['content'],lookup_index(source_task_index(rows),rows[-1]['content']),defs)
        self.assertEqual(rows,old);self.assertEqual(selected[-1],rows[-1]);self.assertEqual(selected[0],rows[0])
        self.assertNotIn('以前的完整解释',json.dumps(selected,ensure_ascii=False))
        self.assertEqual([d['function']['name'] for d in tools],['stock_report_read','stock_public_evidence_read'])
        for q in ('无明确引用','未知 '+('d'*64)):
            self.assertIsNone(selection_view(rows,q,lookup_index(source_task_index(rows),q),defs))
        self.assertTrue(analysis_decision('{"reply_focus":"analysis"}'))
        for text in ('analysis','{"reply_focus":"calculation"}','{"reply_focus":"analysis","extra":true}'):
            self.assertFalse(analysis_decision(text))


class PurposeAgentTests(unittest.IsolatedAsyncioTestCase):
    def agent(self,model,router):
        a=SourceQuoteLoopTests.agent(self,model,router,['stock_report_read','stock_public_evidence_read','stock_research_submit'])
        a.stock_reply_purpose_enabled=True
        return a

    async def test_semantic_analysis_choice_retains_full_history_and_is_not_answer(self):
        seen=[];audit=[]
        class Model:
            cloud=False
            observes_native_requests=True
            async def chat_stream_with_tools(self,messages,tools,**kw):
                seen.append(copy.deepcopy(messages))
                kw['request_observer']('native_model_request',{'parameters':{'enable_thinking':True}})
                if len(seen)==1:
                    yield '{"reply_focus":"analysis"}',None
                    yield '',{'role':'assistant','content':'{"reply_focus":"analysis"}'}
                else:
                    assert any('末尾反证' in (m.get('content') or '') for m in messages)
                    assert any('以前的完整解释' in (m.get('content') or '') for m in messages)
                    yield '保留证据与条件分析。',None
                    yield '',{'role':'assistant','content':'保留证据与条件分析。'}
        a=self.agent(Model(),SimpleNamespace());a.audit.record=lambda e,d:audit.append((e,d))
        events=[v async for v in a.run(history())]
        self.assertEqual(events[-1].text,'保留证据与条件分析。')
        self.assertNotIn('reply_focus',''.join(v.text for v in events if v.kind=='token'))
        self.assertEqual(len(seen),2)
        self.assertEqual([d['reply_purpose_phase'] for e,d in audit if e=='native_model_request'],['selection','explanation'])

    async def test_semantic_calculation_tool_selection_keeps_arguments_and_finishes_once(self):
        seen=[];dispatched=[];params={'reference':REF,'reply_focus':'calculation'}
        class Model:
            cloud=False
            async def chat_stream_with_tools(self,messages,tools,**kw):
                seen.append(messages)
                yield '该文字是选择阶段草稿。',None
                yield '',{'role':'assistant','content':'该文字是选择阶段草稿。','reasoning_content':'hidden-purpose-test','tool_calls':[{
                    'id':'p','function':{'name':'stock_report_read','arguments':copy.deepcopy(params)}}]}
        class Router:
            async def dispatch(self,name,args,session):
                dispatched.append((name,copy.deepcopy(args)));return {'model_observation':value()}
        events=[v async for v in self.agent(Model(),Router()).run(history())]
        self.assertEqual(len(seen),1);self.assertEqual(dispatched,[('stock_report_read',params)])
        self.assertIn('不要求各年均匀',events[-1].text)
        self.assertNotIn('选择阶段草稿',''.join(v.text for v in events if v.kind=='token'))
        self.assertTrue(any(v.kind=='reply_purpose_protocol' for v in events))
        self.assertNotIn('hidden-purpose-test',json.dumps([v.data for v in events],ensure_ascii=False))

    async def test_invalid_decision_or_failed_read_does_not_retry_or_claim_completion(self):
        for mode in ('prose','missing_mean','error','unoffered'):
            calls=[];dispatched=[]
            class Model:
                cloud=False
                async def chat_stream_with_tools(self,*args,**kw):
                    calls.append(1)
                    if mode=='prose':yield '',{'role':'assistant','content':'我已经计算好了。'}
                    else:yield '',{'role':'assistant','content':'','tool_calls':[{'id':'p','function':{
                        'name':'stock_research_submit' if mode=='unoffered' else 'stock_report_read',
                        'arguments':{'reference':REF,'reply_focus':'calculation'}}}]}
            class Router:
                async def dispatch(self,*args):
                    dispatched.append(args)
                    if mode=='error':return {'error':'RULE_BLOCKED'}
                    v=value();v['program_calculations'][0].pop('service_mean');return {'model_observation':v}
            events=[v async for v in self.agent(Model(),Router()).run(history())]
            self.assertEqual(len(calls),1);self.assertEqual(events[-1].kind,'error')
            if mode in ('prose','unoffered'):self.assertFalse(dispatched)

    async def test_answer_only_no_grant_unknown_and_unbound_keep_original_path(self):
        for mode in ('answer_only','no_grant','unknown','unbound'):
            seen=[]
            class Model:
                cloud=mode=='no_grant'
                async def chat_stream_with_tools(self,messages,tools,**kw):
                    seen.append(messages);yield '',{'role':'assistant','content':'原回答路径。'}
            a=self.agent(Model(),SimpleNamespace())
            if mode=='answer_only':a.allow_tools=False
            if mode=='no_grant':a.workspace_cloud_allowed=False
            q='未知 '+('d'*64) if mode=='unknown' else '说明原问题' if mode=='unbound' else None
            events=[v async for v in a.run(history(q))]
            self.assertEqual(len(seen),1);self.assertEqual(events[-1].text,'原回答路径。')
            self.assertFalse(any(v.kind=='reply_purpose_protocol' for v in events))
