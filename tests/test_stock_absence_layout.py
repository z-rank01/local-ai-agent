import unittest
from types import SimpleNamespace
from tests import test_stock_source_quotes as quote_tests
from core.stock_source_quotes import (unverified_absence_claims, may_start_literal_quote,
                                     isolate_quote_paragraphs, quote_feedback)


class AbsenceLayoutTests(unittest.TestCase):
    def test_saved_failure_heading_and_table_are_not_exempt(self):
        for text in ('3. **无投产计划**：缺乏明确的项目启动时间表',
                     '## 无投产计划',
                     '| 无投产计划 → 无法测算收入确认时点 | 难以预测金额 |',
                     '| 缺口 | 无合同条款 | 尚未核对其他资料 |'):
            with self.subTest(text=text):
                self.assertTrue(unverified_absence_claims(text,[]))
                self.assertTrue(may_start_literal_quote(text))

    def test_qualifier_cannot_cover_a_second_sentence_or_cell(self):
        for text in ('公告尚未读完，不能断言公告没有条款。公告没有投产计划。',
                     '公告尚未核对付款方式，因此公告没有付款条款。',
                     '| 目前无法判断利润 | 无投产计划 |',
                     '| 公告尚未读完 | 文件未披露成本 |'):
            with self.subTest(text=text):
                self.assertEqual(len(unverified_absence_claims(text,[])),1)

    def test_conditional_and_limited_knowledge_are_preserved(self):
        for text in ('如果公告没有付款条款，就需要核对合同。',
                     '无投产计划（尚未核对）。',
                     '在当前已读范围，公告未披露投产日期。',
                     '本次检索没有找到计划，不能据此认定没有计划。',
                     '| 已提供材料没有付款条款 | 仍须核对合同 |',
                     '没有足够证据确认项目利润。',
                     '缺乏充分的数据判断需求。',
                     '没有证据证明2026需求已兑现。',
                     '不能从尚未投产推出公告没有计划。'):
            with self.subTest(text=text):
                self.assertFalse(unverified_absence_claims(text,[]))

    def test_actual_unfinished_step_is_not_a_missing_plan(self):
        text='项目尚未正式投产运营；合同尚未签订。收入确认时间待定。'
        self.assertFalse(unverified_absence_claims(text,[]))
        self.assertFalse(may_start_literal_quote(text))

    def test_explicit_attribution_is_clause_local_or_same_table_row(self):
        sources=[{'title':'公告甲','url':'https://example.com/a.pdf'}]
        self.assertFalse(unverified_absence_claims('公告甲未披露投产计划。',sources))
        self.assertTrue(unverified_absence_claims('公告甲已读完。公告乙没有投产计划。',sources))
        self.assertFalse(unverified_absence_claims('| 无投产计划 | [公告甲](https://example.com/a.pdf) |',sources))
        self.assertTrue(unverified_absence_claims('公告未披露计划。',[{'title':'','url':''}]))

    def test_local_isolation_keeps_valid_fact_rows_and_removes_empty_scaffolding(self):
        text=('当前判断：中标尚需签约。\n\n'
              '| 项目 | 状态 |\n| --- | --- |\n| 项目尚未投产 | 公告已说明 |\n| 无投产计划 | 无法预测 |\n\n'
              '## 未核验缺口\n\n| 项目 | 状态 |\n| --- | --- |\n| 无合同条款 | 未见 |\n\n'
              '## 规模参照\n\n营收规模参照约22.8%，不代表当年新增收入。')
        issues=unverified_absence_claims(text,[])
        result=isolate_quote_paragraphs(text,issues)
        self.assertIn('项目尚未投产',result)
        self.assertIn('规模参照约22.8%',result)
        self.assertNotIn('无投产计划',result)
        self.assertNotIn('未核验缺口',result)
        self.assertNotIn('无合同条款',result)

    def test_code_fences_and_unrelated_inference_remain_intact(self):
        text='```text\n公告没有条款。\n\n无投产计划\n```\n\n较高现金流不能单独证明回款质量。'
        self.assertFalse(unverified_absence_claims(text,[]))

    def test_feedback_explains_missing_scope_without_certifying_legal_effects(self):
        text=quote_feedback(unverified_absence_claims('## 无投产计划',[]))
        self.assertIn('只确认某步骤尚未发生',text)
        self.assertIn('不新增研究',text)
        self.assertNotIn('没有法律约束力',text)

    def test_stream_prefix_waits_for_the_claim_before_noun_is_complete(self):
        for text in ('3. **无', '| 无投', '## 缺乏'):
            self.assertTrue(may_start_literal_quote(text))
        self.assertFalse(may_start_literal_quote('合同尚未签订。'))


class AbsenceLoopTests(unittest.IsolatedAsyncioTestCase):
    agent = quote_tests.SourceQuoteLoopTests.agent

    async def test_streamed_heading_is_locally_isolated_without_source_or_research_retry(self):
        calls=[]
        class Model:
            cloud=False
            async def chat_stream_with_tools(self,*args,**kwargs):
                calls.append(1)
                text='3. **无投产计划**：无法预测。\n\n有效事实：项目尚未投产。'
                for token in ('3. **无', '投产', '计划**：无法预测。\n\n有效事实：项目尚未投产。'):
                    yield token,None
                yield '',{'role':'assistant','content':text}
        history=[{'role':'tool','tool_name':'stock_report_read','content':'{}'},
                 {'role':'user','content':'只解释已保存公告，不重新研究。'}]
        events=[e async for e in self.agent(Model(),SimpleNamespace(),[]).run(history)]
        self.assertEqual(len(calls),1)
        self.assertFalse(any(e.kind=='tool_start' for e in events))
        self.assertTrue(any(e.kind=='quality_rejected' for e in events))
        self.assertNotIn('无投产计划',events[-1].text)
        self.assertIn('有效事实：项目尚未投产',events[-1].text)
        self.assertEqual(''.join(e.text for e in events if e.kind=='token'),events[-1].text)
