import json
import unittest
from types import SimpleNamespace

from core.agent import Agent
from plugins.finance.source_quotes import (original_texts, original_records, complete_sources,
    unverified_quotes, unverified_absence_claims, isolate_quote_paragraphs, repair_quote_format)


def source_message(text, kind='ORIGINAL_TEXT', **extra):
    return {'role':'tool','tool_name':'stock_public_evidence_read',
            'content':json.dumps({'kind':'public_evidence_read','excerpts':[{'kind':kind,'text':text}],**extra})}


class SourceQuoteTests(unittest.TestCase):
    def test_discussion_terms_do_not_become_quotes_from_later_source_mention(self):
        for text in ('双方具体的"水处理相关收入"金额及占主营收入比例需定期报告披露的主营构成原文。',
                     '不能仅凭"环保/水务行业名称"认定业务可比，必须以实际收入构成原文为准。'):
            self.assertEqual(unverified_quotes(text, []), [])
        text = '讨论“回款质量”需核对原文。原文写明：“资金已全部收回”。'
        self.assertEqual([x['quote'] for x in unverified_quotes(text, [])], ['资金已全部收回'])
        self.assertTrue(unverified_quotes('“资金已全部收回”的原文尚待核对。', []))

    def test_inability_to_infer_is_not_a_source_absence_claim(self):
        for reply in ('仅凭该文件无法确认2026需求已兑现。', '这份通知无从证明实际销量。'):
            self.assertFalse(unverified_absence_claims(reply,[]))
        for reply in ('该文件没有公布兑付金额。', '原文无结转条款。', '通知未规定资金收回。'):
            self.assertTrue(unverified_absence_claims(reply,[]))

    def test_standalone_source_position_note_not_checked_as_verbatim_prose(self):
        reply='准确原文：\n\n> “真实原句。”\n> *(excerpt_id: aabbcc，正文字符 10-16)*'
        self.assertFalse(unverified_quotes(reply,['真实原句。']))
        self.assertTrue(unverified_quotes('准确原文：\n\n> “虚构原句。”\n> *(excerpt_id: aabbcc)*',['真实原句。']))
    def test_label_not_a_literal_claim_and_whitespace_restored_to_actual_source(self):
        reply='准确原文：\n\n> **原文引文：**\n> “截至 2024 年 12 月 31 日未用完的中央下达资金额度收回中央。”'
        source='截至2024年12月31日未用完的中央下达资金额度收回中央。'
        repaired, edits = repair_quote_format(reply, [source])
        self.assertEqual(len(edits), 1)
        self.assertIn('“'+source+'”', repaired)
        self.assertFalse(unverified_quotes(repaired, [source]))

    def test_trailing_ellipsis_is_editorial_outside_exact_literal_prefix(self):
        reply='原文：“多项支持政策，按照9:1的原则……”'
        repaired, edits = repair_quote_format(reply, ['多项支持政策，按照9:1的原则实行央地共担。'])
        self.assertEqual(repaired, '原文：“多项支持政策，按照9:1的原则”（引文节选，末尾省略）')
        self.assertEqual(edits[0]['reason'], 'TRAILING_ELLIPSIS')
        self.assertFalse(unverified_quotes(repaired, ['多项支持政策，按照9:1的原则实行央地共担。']))

    def test_ambiguous_source_or_changed_dates_numbers_words_not_repaired(self):
        cases=[('原文：“合同 已终止”',['合同  已终止','合同已终止']),
               ('原文：“2026年金额60亿元”',['2024年金额60亿元']),
               ('原文：“金额60亿元”',['金额6亿元']),
               ('原文：“资金已经收回”',['资金应当收回']),
               ('原文：“多项…共担。”',['多项政策实行央地共担。'])]
        for reply, sources in cases:
            self.assertEqual(repair_quote_format(reply,sources),(reply,[]))
            self.assertTrue(unverified_quotes(reply,sources))

    def test_local_repair_only_changes_labelled_quote_not_ordinary_prose(self):
        reply='讨论原计划2024 年。\n\n原句：“2024 年期限”\n\n检索词：“2024 年期限”'
        repaired, _=repair_quote_format(reply,['2024年期限'])
        self.assertIn('原句：“2024年期限”',repaired)
        self.assertIn('讨论原计划2024 年',repaired)
        self.assertIn('检索词：“2024 年期限”',repaired)
    def test_cloud_reference_binds_complete_read_without_private_job_id(self):
        def page(ref, ids, tail=False):
            return source_message('',source_reference=ref,event_id='event',title='通知甲',url='https://example.org/a',
                total_excerpts=2,next_cursor='' if tail else 'cursor',
                excerpts=[{'excerpt_id':str(i),'kind':'ORIGINAL_TEXT','text':'通知原文'+str(i)} for i in ids])
        self.assertFalse(complete_sources(original_records([page('a'*64,[0]),page('b'*64,[1],True)])))
        self.assertFalse(complete_sources(original_records([page('a'*64,[1],True)])))
        sources=complete_sources(original_records([page('a'*64,[0]),page('a'*64,[1],True)]))
        self.assertEqual(sources,[{'title':'通知甲','url':'https://example.org/a'}])
        self.assertFalse(unverified_absence_claims('通知甲未规定结转。',sources))

    def test_original_section_keeps_scope_across_nested_heading_and_blank_blocks(self):
        reply='## 二、准确原文与出处\n\n### 文件信息\n\n日期另列。\n\n### 文中相关条款定位\n\n关于资金的条款为：\n\n> "模型改写的资金条款"\n\n## 三、解释\n\n检索词：“未用完”。'
        self.assertEqual([x['quote'] for x in unverified_quotes(reply,[])],['模型改写的资金条款'])
        self.assertFalse(unverified_quotes(reply,['模型改写的资金条款']))

    def test_unquoted_markdown_original_is_checked_but_explicit_paraphrase_is_not(self):
        reply='## 来源原文\n\n> 订单已经取消。\n> 后续不得确认收入。'
        self.assertEqual(len(unverified_quotes(reply,[])),2)
        self.assertFalse(unverified_quotes(reply,['订单已经取消。后续不得确认收入。']))
        self.assertFalse(unverified_quotes('## 来源转述（非逐字）\n\n> 订单取消。',[]))

    def test_absence_requires_whole_original_and_local_source_attribution(self):
        def page(ids,tail=False,kind='ORIGINAL_TEXT'):
            return source_message('',job_id='job',event_id='event',title='通知甲',url='https://example.org/a',
                total_excerpts=2,next_cursor='' if tail else 'cursor',
                excerpts=[{'excerpt_id':str(i),'kind':kind,'text':'通知原文'+str(i)} for i in ids])
        # Repeated reads and the last page alone cannot prove complete coverage.
        self.assertFalse(complete_sources(original_records([page([1],True),page([1],True)])))
        sources=complete_sources(original_records([page([0]),page([1],True)]))
        self.assertTrue(unverified_absence_claims('文件未规定结转。',sources))
        self.assertTrue(unverified_absence_claims('通知乙未规定结转。',sources))
        self.assertFalse(unverified_absence_claims('通知甲未规定结转。',sources))
        self.assertTrue(unverified_absence_claims('通知甲未规定结转。',[]))
        self.assertFalse(unverified_absence_claims('原文尚未读完，不能断言文件没有结转条款。',[]))
        self.assertFalse(complete_sources(original_records([page([0,1],True,'SOURCE_FIELD')])))

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
    async def test_known_format_defect_repaired_without_provider_or_tool_retry(self):
        calls=[]
        class Model:
            cloud=False
            async def chat_stream_with_tools(self,*args):
                calls.append(1)
                text='依据原文：“2024 年未用完的中央额度收回……”'
                yield text,None
                yield '',{'role':'assistant','content':text}
        history=self.history()+[source_message('2024年未用完的中央额度收回中央。')]
        events=[e async for e in self.agent(Model(),SimpleNamespace(),['stock_public_evidence_read']).run(history)]
        self.assertEqual(len(calls),1)
        self.assertEqual(len([e for e in events if e.kind=='quality_repaired']),1)
        self.assertFalse(any(e.kind in ('quality_rejected','tool_start') for e in events))
        self.assertIn('“2024年未用完的中央额度收回”',events[-1].text)
        self.assertEqual(''.join(e.text for e in events if e.kind=='token'),events[-1].text)
    def agent(self, model, router, tools):
        from core import config
        from core.tool_registry import ToolRegistry
        registered = ToolRegistry(config.TOOLS_DIR, stock_bridge_url='http://unused')
        async def process(messages):return messages
        agent=Agent(llm=model,router=router,registry=SimpleNamespace(get_definitions=lambda **kw:[
            {'type':'function','function':{'name':name}} for name in tools],
            tools_for_role=registered.tools_for_role, execution_policy=registered.execution_policy,
            history_projectors=registered.history_projectors),
            audit=SimpleNamespace(record=lambda *a:None),context_mgr=SimpleNamespace(process=process),
            prompt_builder=SimpleNamespace(build=lambda **kw:'test'),max_rounds=5)
        agent.quality_first_research=agent.stock_loop_enabled=True
        return agent

    def history(self):
        return [{'role':'tool','tool_name':'stock_report_read','tool_call_id':'saved',
                 'content':json.dumps({'answer_pages':[{'body':'已保存模型阶段分析。'}]})},
                {'role':'user','content':'请核对资金回收条款，不创建或恢复研究。'}]

    async def test_unread_absence_claim_is_repaired_by_actual_source_read(self):
        calls=[];runs=[]
        class Model:
            cloud=False
            async def chat_stream_with_tools(self,messages,tools):
                runs.append(1)
                if len(runs)==1:
                    text='结论：文件未规定资金收回。'
                    yield text,None
                    yield '',{'role':'assistant','content':text}
                elif len(runs)==2:
                    yield '',{'role':'assistant','content':'','tool_calls':[{'id':'read-tail','function':{
                        'name':'stock_public_evidence_read','arguments':{'reference':'a'*64,'event_id':'b'*16}}}]}
                else:
                    text='原文：“未用完的中央额度收回”。这不证明下一年度需求兑现。'
                    yield text,None
                    yield '',{'role':'assistant','content':text}
        class Router:
            async def dispatch(self,name,params,session):
                calls.append(name)
                return {'kind':'public_evidence_read','excerpts':[{'kind':'ORIGINAL_TEXT','text':'未用完的中央额度收回。'}]}
        events=[e async for e in self.agent(Model(),Router(),['stock_public_evidence_read']).run(self.history())]
        self.assertEqual(calls,['stock_public_evidence_read'])
        self.assertEqual(len([e for e in events if e.kind=='quality_rejected']),1)
        self.assertNotIn('未规定资金收回',''.join(e.text for e in events if e.kind=='token'))
        self.assertIn('未用完的中央额度收回',events[-1].text)

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
    async def test_program_scale_repair_keeps_raw_output_local_and_correct_final(self):
        from tests.test_stock_calculation_review import history, REF
        conv=await self.install_review_history()
        for message in history():
            self.runtime.store.add_message(conv.id,role='protocol',metadata={'cloud_safe':True,'message':message})
        generated='114001200元 / 500764121.06元 ≈ 2.28倍。履约与回款仍待核。'
        calls=[]
        class Model:
            cloud=True
            model='qwen3.5-flash'
            async def chat_stream_with_tools(self,*args):
                if any('current-question-purpose' in str(m) or '\u672c\u6b65\u9aa4\u4ec5\u6309\u5f53\u524d\u7528\u6237\u95ee\u9898' in m.get('content', '') for m in args[0]):
                    yield '', {'role': 'assistant', 'content': '{"reply_focus":"analysis"}'}
                    return
                calls.append(1)
                yield generated,None
                yield '',{'role':'assistant','content':generated}
            async def close(self):pass
        self.runtime.models.clients['qwen:qwen3.5-flash']=Model()
        events=[e async for e in self.service.stream_chat(ChatRequest(conversation_id=conv.id,
            message='解释已保存报告'+REF,provider_id='qwen',model='qwen3.5-flash'))]
        final=next(e.data['text'] for e in events if e.event=='assistant.completed')
        self.assertIn('0.23倍',final);self.assertNotIn('2.28倍',final)
        self.assertEqual(len(calls),1)
        self.assertEqual(''.join(e.data['text'] for e in events if e.event=='assistant.delta'),final)
        rows=self.runtime.store.get_messages(conv.id)
        repaired=[json.loads(r.metadata) for r in rows if json.loads(r.metadata).get('quality_status')=='repaired_program_scale']
        self.assertEqual(repaired[0]['original_answer'],generated)
        self.assertFalse(repaired[0]['cloud_safe'])
        self.assertFalse(any(generated==m.get('content') for m in self.runtime.store.messages_as_dicts(conv.id,cloud=True)))

    async def test_format_repair_preserves_original_locally_and_final_stream_matches(self):
        conv=await self.install_review_history()
        self.runtime.store.add_message(conv.id,role='protocol',metadata={'cloud_safe':True,'message':{
            'role':'assistant','content':'','tool_calls':[{'id':'raw','type':'function','function':{
                'name':'stock_public_evidence_read','arguments':'{}'}}]}})
        raw=source_message('2024年未用完的中央额度收回中央。');raw['tool_call_id']='raw'
        self.runtime.store.add_message(conv.id,role='protocol',metadata={'cloud_safe':True,'message':raw})
        generated='原文：“2024 年未用完的中央额度收回……”'
        class Model:
            cloud=True
            model='qwen3.5-flash'
            async def chat_stream_with_tools(self,*args):
                yield generated,None
                yield '',{'role':'assistant','content':generated}
            async def close(self):pass
        self.runtime.models.clients['qwen:qwen3.5-flash']=Model()
        events=[e async for e in self.service.stream_chat(ChatRequest(conversation_id=conv.id,
            message='核对已保存原句',provider_id='qwen',model='qwen3.5-flash'))]
        final=next(e.data['text'] for e in events if e.event=='assistant.completed')
        self.assertEqual(''.join(e.data['text'] for e in events if e.event=='assistant.delta'),final)
        self.assertIn('“2024年未用完的中央额度收回”',final)
        rows=self.runtime.store.get_messages(conv.id)
        repaired=[json.loads(r.metadata) for r in rows if json.loads(r.metadata).get('quality_status')=='repaired_literal_format']
        self.assertEqual(repaired[0]['original_answer'],generated)
        self.assertFalse(repaired[0]['cloud_safe'])
        cloud=self.runtime.store.messages_as_dicts(conv.id,cloud=True)
        self.assertFalse(any(generated==m.get('content') for m in cloud))
        local=self.runtime.store.messages_as_dicts(conv.id,cloud=False)
        self.assertTrue(any('确定的局部修复' in m.get('content','') for m in local))
    async def install_review_history(self):
        from core import config
        self.runtime.tool_registry.load_backend(config.TOOLS_DIR, 'stock-bridge')
        conv=self.runtime.store.create_conversation(model='qwen:qwen3.5-flash')
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
                if any('\u672c\u6b65\u9aa4\u4ec5\u6309\u5f53\u524d\u7528\u6237\u95ee\u9898' in m.get('content', '') for m in args[0]):
                    yield '', {'role': 'assistant', 'content': '{"reply_focus":"analysis"}'}
                    return
                calls.append(1);text=bad if len(calls)==1 else good
                yield text,None
                yield '',{'role':'assistant','content':text}
            async def close(self):pass
        self.runtime.models.clients['qwen:qwen3.5-flash']=Model()
        events=[e async for e in self.service.stream_chat(ChatRequest(conversation_id=conv.id,
            message='请核对已保存公开原文',provider_id='qwen',model='qwen3.5-flash'))]
        visible=[e.data.get('text','') for e in events if e.event=='assistant.completed']
        # A replacement paragraph is not a source-read receipt. The current
        # source protocol must keep this run incomplete instead of accepting it.
        self.assertTrue(any(e.event == 'error' for e in events))
        self.assertTrue(any('来源补查未返回工具调用' in text for text in visible))
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
        events=[e async for e in self.service.stream_chat(ChatRequest(conversation_id=conv.id,
            message='解释已保存报告',provider_id='qwen',model='qwen3.5-flash'))]
        run_id = next(e.run_id for e in events if e.run_id)
        self.assertEqual(self.service.runs.store.get(run_id)['status'], 'STOPPED')
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
