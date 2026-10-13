import copy,json,unittest
from types import SimpleNamespace
from tests import test_stock_calculation_review as calculations, test_stock_source_quotes as quotes
from core.stock_calculation_review import calculation_index, validated_annual_source, repair_scale_units
from core.stock_source_quotes import isolate_quote_paragraphs,unverified_quotes,prune_empty_sections

def annual_source():
    return dict(kind='SOURCE_FIELD',metric='revenue',symbol='688178',report_period='20251231',
        value_yuan='500764121.06',unit='CNY',url='https://example.org/finance',location='利润表/营业总收入',
        event_id='f'*16,read_cursor='120:abcdefabcdefabcd',field='营业总收入')

class ProgramSourceTests(unittest.TestCase):
    def test_bad_optional_source_cannot_replace_good_numbers(self):
        for change in [dict(report_period='20260630'),dict(value_yuan='70000000'),dict(symbol='300070'),
                       dict(unit='USD'),dict(url='http://127.0.0.1/private'),dict(read_cursor='bad')]:
            value=calculations.observation();value['program_calculations'][0]['annual_source']=dict(annual_source(),**change)
            before=copy.deepcopy(value)
            row=calculation_index(calculations.history(value))[calculations.REF][0]
            self.assertNotIn('annual_source',row);self.assertEqual(row['annual_period'],'20251231')
            self.assertEqual(before,value)
        row=calculations.receipt();row['annual_source']=annual_source()
        self.assertEqual(validated_annual_source(row),annual_source())

    def test_only_bound_unit_conflict_fixed(self):
        text='| 指标 | 数值 | 单位 |\n| --- | --- | --- |\n| 合同总额 | **11,400.12 万元** | 元 |\n| 其他金额 | 88万元 | 元 |'
        result,edits=repair_scale_units(text,calculation_index(calculations.history())[calculations.REF])
        self.assertEqual(len(edits),1);self.assertIn('| 11,400.12 | 万元 |',result)
        self.assertIn('| 其他金额 | 88万元 | 元 |',result)
        self.assertEqual(repair_scale_units(text,[]),(text,[]))

    def test_isolation_removes_empty_sections_and_tables_not_counterevidence_or_code(self):
        text='# 报告\n\n## 原文\n\n> “未核验原句”\n\n---\n\n## 财务\n\n| 指标 | 数值 |\n| --- | --- |\n| 原文表述 | “坏引文” |\n\n## 反证\n\n后文合同可能取消。\n\n```md\n## 示例标题\n| 项目 | 内容 |\n| --- | --- |\n```'
        result=isolate_quote_paragraphs(text,unverified_quotes(text,[]))
        self.assertNotIn('## 原文',result);self.assertNotIn('## 财务',result)
        self.assertIn('后文合同可能取消',result);self.assertIn('## 示例标题',result)
        self.assertIn('部分引述',result)
        self.assertEqual(prune_empty_sections('## 实质\n\n- 反证仍在。'),'## 实质\n\n- 反证仍在。')

class ProgramSourceLoopTests(unittest.IsolatedAsyncioTestCase):
    agent=quotes.SourceQuoteLoopTests.agent
    async def test_current_question_is_last_in_delivery_without_mutating_saved_history(self):
        seen=[]
        current='只核对年度字段和披露日期，不分析中标项目。'
        history=calculations.history()+[dict(role='user',content=current)]
        snapshot=copy.deepcopy(history)
        class Model:
            cloud=False
            async def chat_stream_with_tools(self,messages,tools):
                seen.append(messages)
                yield '已核对当前字段。',None;yield '',dict(role='assistant',content='已核对当前字段。')
        events=[e async for e in self.agent(Model(),SimpleNamespace(),['stock_report_read']).run(history)]
        self.assertEqual(events[-1].kind,'done')
        self.assertEqual(seen[0][-1]['role'],'system')
        self.assertEqual(json.loads(seen[0][-1]['content'].split('\n')[-1])['current_user_message'],current)
        self.assertEqual(history,snapshot)
        self.assertEqual(sum(m.get('content')==current for m in seen[0]),1)

    async def test_disabled_cloud_read_does_not_add_new_program_source_view(self):
        seen=[]
        class Model:
            cloud=True
            async def chat_stream_with_tools(self,messages,tools):
                seen.extend(messages)
                yield '只讨论已提供材料。',None
                yield '',dict(role='assistant',content='只讨论已提供材料。')
        agent=self.agent(Model(),SimpleNamespace(),['stock_report_read'])
        agent.workspace_cloud_allowed=False
        events=[e async for e in agent.run(calculations.history()+[dict(role='user',content=calculations.REF)])]
        self.assertEqual(events[-1].kind,'done')
        self.assertFalse(any(m.get('role')=='system' and '[当前任务已授权程序规模参照' in m.get('content','') for m in seen))

    async def test_source_field_read_can_return_to_full_context_without_original_quote(self):
        seen=[]
        value=calculations.observation();value['program_calculations'][0]['annual_source']=annual_source()
        class Model:
            cloud=False
            async def chat_stream_with_tools(self,messages,tools):
                seen.append(messages)
                if len(seen)==1:
                    text='原文：“未经核对的句子”。'
                    yield text,None;yield '',dict(role='assistant',content=text)
                elif len(seen)==2:
                    yield '',dict(role='assistant',content='',tool_calls=[dict(id='annual-read',function=dict(
                        name='stock_public_evidence_read',arguments=dict(reference=calculations.REF,
                            event_id='f'*16,cursor=annual_source()['read_cursor'])))])
                elif len(seen)==3:
                    # Valid field reading can finish lookup; it is not a quote.
                    yield '字段已取得。',None;yield '',dict(role='assistant',content='字段已取得。')
                else:
                    text='2025年度来源字段营收500764121.06元。比值22.77%，不代表当年收入。'
                    yield text,None;yield '',dict(role='assistant',content=text)
        class Router:
            async def dispatch(self,name,params,session):
                assert name=='stock_public_evidence_read' and params['cursor']==annual_source()['read_cursor']
                return dict(kind='public_evidence_read',source_reference=calculations.REF,event_id='f'*16,next_cursor='140:abcdefabcdefabcd',
                    excerpts=[dict(kind='SOURCE_FIELD',text='营业总收入500764121.06元；期间20251231',
                        url='https://example.org/finance',location='利润表/营业总收入')])
        agent=self.agent(Model(),Router(),['stock_report_read','stock_public_evidence_read','stock_public_evidence_find'])
        events=[e async for e in agent.run(calculations.history(value)+[dict(role='user',content='解释报告'+calculations.REF)])]
        self.assertEqual(events[-1].kind,'done')
        self.assertIn('22.77%',events[-1].text)
        self.assertIn('SOURCE_FIELD',json.dumps(seen[1],ensure_ascii=False))
        self.assertIn('500764121.06',json.dumps(seen[-1],ensure_ascii=False))
        self.assertFalse(any('program_source_view' in m for view in seen for m in view))
