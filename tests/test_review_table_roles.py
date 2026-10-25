import unittest
import json
from plugins.finance.source_quotes import unverified_quotes, unverified_absence_claims, repair_quote_format, isolate_quote_paragraphs


class ReviewTableRoleTests(unittest.TestCase):
    def test_prior_claim_quotes_are_not_source_quotes(self):
        text = '| 待检陈述 | 判定 | 原文依据 |\n| --- | --- | --- |\n| 原文写明：“旧错误” | 尚不能成立 | “真实原句” |'
        self.assertEqual(unverified_quotes(text, ['真实原句']), [])

    def test_source_column_is_still_checked(self):
        text = '| 原答陈述 | 判定 | 原文依据 |\n| --- | --- | --- |\n| “有限” | 尚不能成立 | “改写成的原句” |'
        issues = unverified_quotes(text, [])
        self.assertEqual([row['quote'] for row in issues], ['改写成的原句'])
        self.assertEqual(issues[0]['cell'], 2)

    def test_required_premise_none_is_not_absence_of_data(self):
        text = '| 编号 | 缺少的必要前提 |\n| --- | --- |\n| P7 | 无（利润数据为事实） |\n| P6 | 缺乏关于审批时效的数据 |'
        self.assertFalse(unverified_absence_claims(text, []))

    def test_explicit_document_absence_in_premise_cell_still_needs_read(self):
        text = '| 编号 | 缺少的必要前提 |\n| --- | --- |\n| P7 | 公告未披露收入安排 |'
        self.assertTrue(unverified_absence_claims(text, []))

    def test_reviewed_absence_claim_not_new_publication_assertion(self):
        text = '| 待检陈述 | 判定 |\n| --- | --- |\n| 公告没有投产计划 | 尚不能成立 |'
        self.assertFalse(unverified_absence_claims(text, []))

    def test_untyped_absence_table_keeps_existing_gate(self):
        text = '| 事项 | 当前状态 |\n| --- | --- |\n| 履约 | 无投产计划 |'
        self.assertTrue(unverified_absence_claims(text, []))

    def test_incidental_word_original_does_not_label_every_quoted_term(self):
        text = '| 编号 | 说明 |\n| --- | --- |\n| P9 | “战略意义”不能由原文的积极影响推出 |'
        self.assertFalse(unverified_quotes(text, []))

    def test_explicit_literal_quote_inside_commentary_still_checked(self):
        text = '| 编号 | 说明 |\n| --- | --- |\n| P9 | 原文：“未来已经兑现” |'
        self.assertTrue(unverified_quotes(text, []))

    def test_explicit_source_list_inside_commentary_keeps_each_quote_checked(self):
        text='| 编号 | 说明 |\n| --- | --- |\n| P1 | 原文明确表示：“合同取消”、“收入为零” |'
        self.assertEqual([x['quote'] for x in unverified_quotes(text,['合同取消'])],['收入为零'])

    def test_postposed_original_label_keeps_actual_bad_source_quote_checked(self):
        text='| 编号 | 处理动作 |\n| --- | --- |\n| P5 | 引用“分期实施”“跨年实施”原文 |'
        self.assertEqual([x['quote'] for x in unverified_quotes(text,['跨年分期实施'])],['跨年实施'])

    def test_comparison_with_original_is_not_postposed_literal(self):
        text='| 编号 | 说明 |\n| --- | --- |\n| P1 | 比较“有限”与原文“不确定性” |'
        self.assertFalse(unverified_quotes(text,['不确定性']))

    def test_legacy_row_label_remains_literal(self):
        text = '| 项目 | 详情 |\n| --- | --- |\n| 原文表述 | “假句” |'
        self.assertTrue(unverified_quotes(text, []))

    def test_whitespace_repair_changes_only_source_cell_not_identical_claim(self):
        text = '| 待检陈述 | 原文依据 |\n| --- | --- |\n| “尚未签约” | “尚未签约” |'
        repaired, edits = repair_quote_format(text, ['尚未 签约'])
        self.assertEqual(len(edits), 1)
        self.assertIn('| “尚未签约” | “尚未 签约” |', repaired)

    def test_tables_do_not_leak_literal_scope_into_following_prose(self):
        text = '| 项目 | 原文依据 |\n| --- | --- |\n| 事实 | “真实原句” |\n\n分析比较“有限”和“不确定”。'
        self.assertFalse(unverified_quotes(text, ['真实原句']))


def payload(reason='现有依据不足以得出该结论', missing=()):
    return json.dumps({'decisions':[{'claim_id':'P1','status':'NOT_ESTABLISHED',
        'evidence_ids':['E1'],'missing_premises':list(missing),'reason':reason,
        'counterexample':{'text':'若年度实施时间不同，结论可能不成立',
            'consistent_with_evidence':True,'contradicts_claim':True}}]},ensure_ascii=False)


class DeclaredReviewRoleTests(unittest.TestCase):
    def test_data_keys_are_not_source_quotes(self):
        self.assertFalse(unverified_quotes(payload('原文信息不足以支持大小判断'),[]))

    def test_compared_terms_do_not_become_literal_source(self):
        text=payload('原文“不确定性”不能直接支持“有限”')
        self.assertFalse(unverified_quotes(text,['不确定性']))

    def test_explicit_fake_original_in_data_reason_is_checked(self):
        issues=unverified_quotes(payload('原文：“已经确认收入”'),[])
        self.assertEqual([x['quote'] for x in issues],['已经确认收入'])

    def test_modified_introduction_and_literal_list_still_checked(self):
        text=payload('原文明确表示：“已经取消”、“当年收入为零”')
        issues=unverified_quotes(text,['已经取消'])
        self.assertEqual([x['quote'] for x in issues],['当年收入为零'])

    def test_missing_premise_list_is_not_publication_absence(self):
        self.assertFalse(unverified_absence_claims(payload(missing=['缺乏关于审批时效的数据']),[]))
        self.assertTrue(unverified_absence_claims(payload('公告未披露收入安排'),[]))

    def test_whitespace_repair_only_changes_introduced_source_quote(self):
        text=payload('比较“尚未签约”；原文：“尚未签约”')
        repaired,edits=repair_quote_format(text,['尚未 签约'])
        self.assertEqual(len(edits),1)
        reason=json.loads(repaired)['decisions'][0]['reason']
        self.assertEqual(reason,'比较“尚未签约”；原文：“尚未 签约”')

    def test_isolated_review_cannot_be_mistaken_for_certified_declaration(self):
        text=payload('原文：“假的”');issues=unverified_quotes(text,[])
        isolated=json.loads(isolate_quote_paragraphs(text,issues))
        self.assertEqual(isolated['source_check'],'UNVERIFIED')
        self.assertEqual(isolated['decisions'][0]['status'],'NOT_ESTABLISHED')

    def test_unrecognised_json_does_not_expand_role_exemption(self):
        text=json.dumps({'reason':'原文：“没有核验的句子”','confidence':90},ensure_ascii=False)
        self.assertTrue(unverified_quotes(text,[]))
        nested='原文："他说“没有核验的句子”"'
        self.assertEqual([row['quote'] for row in unverified_quotes(nested, [])],
                         ['他说“没有核验的句子”'])
