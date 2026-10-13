import copy,json,unittest
from core.stock_task_source_view import lookup_index
from core.stock_source_quotes import prune_empty_sections,isolate_quote_paragraphs,unverified_quotes
from tests import test_stock_task_source_view as fixture


class FocusedTaskIndexTests(unittest.TestCase):
    def setUp(self):
        self.rows=[{'source_reference':fixture.OLD,'sources':[{'event_id':'b'*16,'read_cursor':'old'}]},
                   {'source_reference':fixture.REF,'sources':[{'event_id':'e'*16,'read_cursor':'current'}]}]

    def test_explicit_task_keeps_exact_cursor_without_rewriting_original_index(self):
        before=copy.deepcopy(self.rows);view=lookup_index(self.rows,fixture.REF)
        self.assertEqual(view['rows'],[self.rows[1]]);self.assertEqual(view['selection'],'explicit_known')
        self.assertEqual(self.rows,before);self.assertEqual(view['version'],'current-task-lookup-index-v1')

    def test_multiple_tasks_and_unbound_questions_retain_scope(self):
        self.assertEqual(lookup_index(self.rows,fixture.REF+' '+fixture.OLD)['rows'],self.rows)
        self.assertEqual(lookup_index(self.rows,'比较研究方法')['rows'],self.rows)
        self.assertIsNone(lookup_index(self.rows,'比较研究方法')['version'])

    def test_unknown_reference_does_not_fall_back_to_different_known_task(self):
        view=lookup_index(self.rows,'d'*64)
        self.assertEqual(view['rows'],[]);self.assertEqual(view['selection'],'explicit_partial')
        self.assertIn('不能以其他已知引用代替',view['note'])
        partial=lookup_index(self.rows,fixture.REF+' '+'d'*64)
        self.assertEqual(partial['rows'],[self.rows[1]])


class OrphanQuoteSectionTests(unittest.TestCase):
    def test_real_failed_answer_shape_removes_empty_heading_and_attribution(self):
        text='### 依据\n\n#### 1. 核心风险提示\n\n*注：以上均为公告原文摘录，来源见下表*\n\n#### 2. 规模参照\n\n| 年度营收 | 5.01亿元 |'
        result=prune_empty_sections(text)
        self.assertNotIn('核心风险提示',result);self.assertNotIn('以上均为',result)
        self.assertIn('5.01亿元',result);self.assertIn('### 依据',result)

    def test_valid_quote_note_and_code_fence_remain(self):
        for text in ('## 原文\n\n> "合同尚未签订"\n\n*注：以上均为公告原文摘录，来源见下表*',
                     '```markdown\n## 空标题\n*注：以上均为公告原文摘录，来源见下表*\n```'):
            self.assertEqual(prune_empty_sections(text),text)

    def test_fact_bearing_note_not_mistaken_for_empty_attribution(self):
        text='## 来源限制\n\n注：以上原文只保存1259字，不能证明网站完整采集。'
        self.assertEqual(prune_empty_sections(text),text)

    def test_isolation_keeps_independent_result_and_visible_gap(self):
        text='规模约22.8%，只作参照。\n\n## 核心风险提示（逐字引文）\n\n> "错误改写"\n\n*注：以上均为公告原文摘录，来源见下表*\n\n## 继续研究\n\n需要实际签约和服务开始的证据。'
        result=isolate_quote_paragraphs(text,unverified_quotes(text,[]))
        self.assertNotIn('## 核心风险提示',result);self.assertNotIn('以上均为',result)
        self.assertIn('22.8%',result);self.assertIn('仍有缺口',result)


class FocusedTaskLoopTests(unittest.IsolatedAsyncioTestCase):
    agent=fixture.CurrentTaskSourceLoopTests.agent

    async def test_actual_repair_step_gets_current_index_and_keeps_thinking_auto(self):
        captured=[];observations=[];dispatched=[]
        class Model:
            cloud=False
            observes_native_requests=True
            async def chat_stream_with_tools(self,messages,tools,**kwargs):
                captured.append(copy.deepcopy(messages));n=len(captured)
                if kwargs.get('request_observer'):
                    kwargs['request_observer']('native_model_request',{'parameters':{'enable_thinking':True,'model':'qwen3.5-flash','stream':True}})
                if n==1:yield '',{'role':'assistant','content':'准确原文：“错误改写”。'}
                elif n in (2,3):
                    name='stock_public_evidence_find' if n==2 else 'stock_public_evidence_read'
                    args={'reference':fixture.REF}
                    args.update({'search':'来源'} if n==2 else {'event_id':fixture.fixtures.EVENT,'cursor':'actual'})
                    yield '',{'role':'assistant','content':'','tool_calls':[{'id':str(n),'function':{'name':name,'arguments':args}}]}
                else:yield '',{'role':'assistant','content':'原文：“来源段落1”。是否兑现仍需核对。'}
        class Router:
            async def dispatch(self,name,params,session):
                dispatched.append(copy.deepcopy(params))
                if name.endswith('find'):return {'kind':'public_evidence_find','source_reference':fixture.REF,
                    'items':[{'event_id':fixture.fixtures.EVENT,'read_cursor':'actual'}]}
                v=fixture.fixtures.page((1,2));v['next_cursor']='';return v
        agent=self.agent(Model(),Router(),['stock_public_evidence_find','stock_public_evidence_read'])
        agent.audit.record=lambda name,row:observations.append((name,row))
        rows=fixture.receipt(fixture.OLD,'old')+fixture.receipt(fixture.REF)+[{'role':'user','content':fixture.REF}]
        events=[e async for e in agent.run(rows)]
        self.assertEqual(next(e.text for e in events if e.kind=='done'),'原文：“来源段落1”。是否兑现仍需核对。')
        self.assertEqual(len(captured),4)
        for frame in captured[1:3]:
            serialized=json.dumps(frame,ensure_ascii=False)
            self.assertIn(fixture.REF,serialized);self.assertNotIn(fixture.OLD,serialized)
        self.assertTrue(all(p['reference']==fixture.REF for p in dispatched))
        native=[r for n,r in observations if n=='native_model_request']
        self.assertTrue(all(r['source_lookup_index_version']=='current-task-lookup-index-v1' and r['source_lookup_reference_count']==1 for r in native))
        self.assertTrue(all(r['parameters']['enable_thinking'] is True and 'tool_choice' not in r['parameters'] for r in native))
