import json
import unittest
from types import SimpleNamespace

from core.agent import Agent
from core.stock_source_quotes import original_texts, unverified_quotes, isolate_quote_paragraphs


def source_message(text, kind='ORIGINAL_TEXT', **extra):
    return {'role':'tool','tool_name':'stock_public_evidence_read',
            'content':json.dumps({'kind':'public_evidence_read','excerpts':[{'kind':kind,'text':text}],**extra})}


class SourceQuoteTests(unittest.TestCase):
    def test_model_summary_search_and_financial_fields_are_not_literal_originals(self):
        text='国家直接安排安徽省60亿元'
        messages=[{'role':'user','content':text},
                  {'role':'tool','tool_name':'stock_report_read','content':json.dumps({'answer_pages':[{'body':text}]})},
                  {'role':'tool','tool_name':'stock_public_evidence_find','content':json.dumps({'items':[{'title':text}]})},
                  source_message(text,'SOURCE_FIELD'),source_message(text,'AI_COMPARABILITY_INFERENCE')]
        self.assertEqual(original_texts(messages),[])
        self.assertTrue(unverified_quotes('原文表述："'+text+'"',original_texts(messages)))

    def test_exact_spaces_and_late_counterevidence_are_preserved(self):
        originals=original_texts([source_message('前文。'*6000+'合同  已终止，旧订单不能计入。')])
        self.assertFalse(unverified_quotes('原文写明：“合同  已终止”',originals))
        self.assertTrue(unverified_quotes('原文写明：“合同已终止”',originals))
        self.assertTrue(unverified_quotes('原文写明：“合同 已终止”',originals))

    def test_labelled_paraphrase_and_search_terms_do_not_pretend_to_be_quotes(self):
        self.assertFalse(unverified_quotes('按阶段分析转述，资金并非农机专属。\n\n检索词：“未用完”。',[]))
        self.assertFalse(unverified_quotes('关键条款（转述）："资金覆盖多项政策"',[]))

    def test_real_failure_shape_and_local_isolation(self):
        reply='主判断：不能据此证明需求兑现。\n\n| 项目 | 详情 |\n| --- | --- |\n| **60亿元条款位置** | "国家直接安排安徽省60亿元" |\n\n下一步核对实际拨付。'
        originals=original_texts([source_message('国家发展改革委、财政部直接安排我省60亿元')])
        issues=unverified_quotes(reply,originals)
        self.assertEqual([x['quote'] for x in issues],['国家直接安排安徽省60亿元'])
        repaired=isolate_quote_paragraphs(reply,issues)
        self.assertIn('不能据此证明需求兑现',repaired)
        self.assertIn('实际拨付',repaired)
        self.assertNotIn('国家直接安排安徽省60亿元',repaired)

    def test_failed_and_malformed_receipts_cannot_verify_original(self):
        self.assertEqual(original_texts([source_message('原文',status='RULE_BLOCKED'),
            {'role':'tool','tool_name':'stock_public_evidence_read','content':'broken'}]),[])

    def test_one_bad_table_row_does_not_remove_independent_numbers(self):
        reply='| 项目 | 详情 |\n| --- | --- |\n| 已核对营收 | 100万元 |\n| 原文表述 | “没有来源的引文” |\n| 期间 | 2026半年报 |'
        result=isolate_quote_paragraphs(reply,unverified_quotes(reply,[]))
        self.assertIn('100万元',result)
        self.assertIn('2026半年报',result)
        self.assertNotIn('没有来源的引文',result)

    def test_literal_label_can_introduce_next_markdown_block(self):
        for reply in ('准确原文：\n\n> “未核验的原句”', '## 原文\n\n> "未核验的原句"',
                      '原文条款："未核验的原句"', '原文关键句：“未核验的原句”',
                      '准确引文：“未核验的原句”', '原句：“未核验的原句”'):
            self.assertTrue(unverified_quotes(reply,[]))
            self.assertFalse(unverified_quotes(reply,['这里有未核验的原句。']))

    def test_legacy_original_and_peer_original_remain_usable(self):
        legacy={'role':'tool','tool_name':'stock_public_evidence_read','content':json.dumps({
            'kind':'public_evidence_read','excerpts':[{'text':'旧版原文'}]})}
        self.assertEqual(original_texts([legacy,source_message('同行原文','PEER_ORIGINAL_TEXT')]),['旧版原文','同行原文'])

    def test_archived_original_needs_complete_authorised_record(self):
        body=source_message('末尾资金收回条款')['content'];cut=len(body)//2
        def page(role,name,offset,text):
            return {'role':'tool','tool_name':'context_evidence_read','content':json.dumps({
                'evidence_id':'known','metadata':{'role':role,'tool_name':name},
                'cursor':offset,'content':text,'total_chars':len(body)})}
        first=page('tool','stock_public_evidence_read',0,body[:cut])
        tail=page('tool','stock_public_evidence_read',cut,body[cut:])
        self.assertEqual(original_texts([first]),[])
        self.assertEqual(original_texts([first,tail]),['末尾资金收回条款'])
        self.assertEqual(original_texts([page('user','stock_public_evidence_read',0,body)]),[])


class SourceQuoteLoopTests(unittest.IsolatedAsyncioTestCase):
    def agent(self, model, router, tools):
        async def process(messages):return messages
        agent=Agent(llm=model,router=router,registry=SimpleNamespace(get_definitions=lambda **kw:[
            {'type':'function','function':{'name':name}} for name in tools]),
            audit=SimpleNamespace(record=lambda *a:None),context_mgr=SimpleNamespace(process=process),
            prompt_builder=SimpleNamespace(build=lambda **kw:'test'),max_rounds=5)
        agent.quality_first_research=agent.stock_loop_enabled=True
        return agent

    def history(self):
        return [{'role':'tool','tool_name':'stock_report_read','tool_call_id':'saved',
                 'content':json.dumps({'answer_pages':[{'body':'已保存模型阶段分析。'}]})},
                {'role':'user','content':'请核对资金回收条款，不创建或恢复研究。'}]

    async def test_rejected_literal_reads_source_then_delivers_without_mutating_tools(self):
        seen,calls=[],[]
        class Model:
            cloud=False
            async def chat_stream_with_tools(self,messages,tools):
                seen.append([x['function']['name'] for x in (tools or [])])
                if len(seen)==1:
                    yield '原句：“未经核验的内容”',None
                    yield '',{'role':'assistant','content':'原句：“未经核验的内容”'}
                elif len(seen)==2:
                    yield '',{'role':'assistant','content':'','tool_calls':[{'id':'r','function':{
                        'name':'stock_public_evidence_read','arguments':{'reference':'a'*64,'event_id':'b'*16}}}]}
                else:
                    yield '原文写明：“未用完的中央额度收回”。不能证明需求兑现。',None
                    yield '',{'role':'assistant','content':'原文写明：“未用完的中央额度收回”。不能证明需求兑现。'}
        class Router:
            async def dispatch(self,name,params,session):
                calls.append(name)
                return {'kind':'public_evidence_read','excerpts':[{'kind':'ORIGINAL_TEXT','text':'截至年底未用完的中央额度收回。'}]}
        agent=self.agent(Model(),Router(),['stock_report_read','stock_public_evidence_read','stock_research_submit'])
        events=[e async for e in agent.run(self.history())]
        self.assertEqual(calls,['stock_public_evidence_read'])
        self.assertNotIn('stock_research_submit',seen[1])
        self.assertEqual(len([e for e in events if e.kind=='quality_rejected']),1)
        self.assertNotIn('未经核验的内容',''.join(e.text for e in events if e.kind=='token'))
        self.assertIn('未用完的中央额度收回',events[-1].text)
        self.assertEqual(events[-1].kind,'done')

    async def test_two_repairs_isolate_only_defect_without_infinite_calls(self):
        runs=[]
        class Model:
            cloud=False
            async def chat_stream_with_tools(self,*args):
                runs.append(1)
                text='已核对金额规模。\n\n原文称：“无来源引文”。\n\n仍应观察订单兑现。'
                yield text,None
                yield '',{'role':'assistant','content':text}
        class Router:
            async def dispatch(self,*args):raise AssertionError('no implicit task submission')
        events=[e async for e in self.agent(Model(),Router(),['stock_public_evidence_read']).run(self.history())]
        self.assertEqual(len(runs),3)
        self.assertIn('已核对金额规模',events[-1].text)
        self.assertNotIn('无来源引文',events[-1].text)
        self.assertIn('来源核验仍有缺口',events[-1].text)
        self.assertEqual(events[-1].kind,'done')

    async def test_provider_failure_is_not_retried_and_partial_stays_available(self):
        runs=[]
        class Model:
            cloud=False
            async def chat_stream_with_tools(self,*args):
                runs.append(1)
                yield '原文引述：已生成的部分成果',None
                raise RuntimeError('provider unknown')
        agent=self.agent(Model(),SimpleNamespace(),['stock_public_evidence_read'])
        events=[e async for e in agent.run(self.history())]
        self.assertEqual(len(runs),1)
        self.assertEqual(agent.pending_source_review,'原文引述：已生成的部分成果')
        self.assertEqual(events[-1].kind,'error')


from tests.test_in1 import ServiceTests
from bff.schemas import ChatRequest


class SourceQuoteServiceTests(ServiceTests):
    async def install_review_history(self):
        conv=self.runtime.store.create_conversation()
        self.runtime.store.add_message(conv.id,role='protocol',metadata={'cloud_safe':True,'message':{
            'role':'assistant','content':'','tool_calls':[{'id':'saved','type':'function','function':{
                'name':'stock_report_read','arguments':'{}'}}]}})
        self.runtime.store.add_message(conv.id,role='protocol',metadata={'cloud_safe':True,'message':{
            'role':'tool','tool_name':'stock_report_read','tool_call_id':'saved','content':'已保存阶段分析'}})
        class Bridge:
            def set_turn(self,*args,**kwargs):pass
            def has_open_turn(self,*args):return False
            async def finish_turn(self,*args):return []
            async def close(self):pass
        self.runtime.stock_bridge=Bridge()
        return conv

    async def test_failed_quote_saved_locally_but_not_published_as_final_fact(self):
        conv=await self.install_review_history();calls=[]
        bad='原文表述：“未经核验的内容”。'
        good='按阶段分析转述，原文仍待核对；目前不能确认资金兑现。'
        class Model:
            cloud=True
            model='qwen3.5-flash'
            async def chat_stream_with_tools(self,*args):
                calls.append(1);text=bad if len(calls)==1 else good
                yield text,None
                yield '',{'role':'assistant','content':text}
            async def close(self):pass
        self.runtime.models.clients['qwen:qwen3.5-flash']=Model()
        events=[e async for e in self.service.stream_chat(ChatRequest(conversation_id=conv.id,
            message='请核对已保存公开原文',provider_id='qwen',model='qwen3.5-flash'))]
        visible=[e.data.get('text','') for e in events if e.event=='assistant.completed']
        self.assertEqual(visible,[good])
        self.assertNotIn(bad,''.join(e.data.get('text','') for e in events if e.event=='assistant.delta'))
        rows=self.runtime.store.get_messages(conv.id)
        rejected=[json.loads(r.metadata) for r in rows if json.loads(r.metadata).get('quality_status')=='rejected_source_quote']
        self.assertEqual(rejected[0]['rejected_answer'],bad)
        view=self.runtime.store.messages_as_dicts(conv.id,cloud=True)
        self.assertFalse(any(m.get('role')=='assistant' and m.get('content')==bad for m in view))

    async def test_cancel_retains_buffered_visible_partial_without_new_source_call(self):
        import asyncio
        conv=await self.install_review_history()
        class Model:
            cloud=True
            model='qwen3.5-flash'
            async def chat_stream_with_tools(self,*args):
                yield '原文引述：原始部分输出，尚未完成。',None
                raise asyncio.CancelledError()
            async def close(self):pass
        self.runtime.models.clients['qwen:qwen3.5-flash']=Model()
        with self.assertRaises(asyncio.CancelledError):
            _=[e async for e in self.service.stream_chat(ChatRequest(conversation_id=conv.id,
                message='解释已保存报告',provider_id='qwen',model='qwen3.5-flash'))]
        rows=self.runtime.store.get_messages(conv.id)
        partial=next(r for r in rows if r.role=='assistant')
        self.assertIn('原始部分输出',partial.content)
        self.assertEqual(json.loads(partial.metadata)['status'],'interrupted')

    async def test_answer_only_isolates_false_quote_without_tools_or_repair_calls(self):
        conv=await self.install_review_history();calls=[];offered=[]
        class Model:
            cloud=True
            model='qwen3.5-flash'
            async def chat_stream_with_tools(self,messages,tools):
                offered.append(tools);calls.append(1)
                yield '',{'role':'assistant','content':'已保存判断仍成立。\n\n原文称：“错误逐字引文”。'}
            async def close(self):pass
        model=Model();self.runtime.models.clients['qwen:qwen3.5-flash']=model
        events=[e async for e in self.service.stream_chat(ChatRequest(conversation_id=conv.id,
            message='只根据已给材料回答，不读新资料',provider_id='qwen',model='qwen3.5-flash',allow_tools=False))]
        self.assertEqual(offered,[None])
        self.assertEqual(calls,[1])
        self.assertFalse(any(e.event=='tool.started' for e in events))
        answer=next(e.data['text'] for e in events if e.event=='assistant.completed')
        self.assertIn('已保存判断仍成立',answer)
        self.assertNotIn('错误逐字引文',answer)
