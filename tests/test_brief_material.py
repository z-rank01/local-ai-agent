import copy
import json
import unittest

from core.brief_material import prepare_material
from tests.test_stock_financial_receipts import observation as financial
from tests.test_stock_company_context import observation as comparison

REF = 'b' * 64


def history(value, name='stock_public_evidence_read', call='read'):
    ref = value['source_reference']
    return [{'role': 'assistant', 'tool_calls': [{'id': call, 'function': {
        'name': name, 'arguments': json.dumps({'reference': ref})}}]},
        {'role': 'tool', 'tool_name': name, 'tool_call_id': call,
         'content': json.dumps({'model_observation': value})}]


def original():
    return {'kind': 'public_evidence_read', 'source_reference': REF, 'event_id': 'a'*16,
            'excerpts': [{'kind': 'ORIGINAL_TEXT', 'excerpt_id': 'source1',
                          'text': '尚未签订正式合同，业绩影响不确定。',
                          'url': 'https://pdf.dfcfw.com/test.pdf', 'location': '页1'}]}


class BriefMaterialTests(unittest.TestCase):
    def prepare(self, rows):
        return prepare_material(rows, [REF], cloud_allowed=True)

    def test_permission_and_unknown_reference_fail_without_fallback(self):
        for allowed in (False, None, 1):
            with self.assertRaises(PermissionError):
                prepare_material(history(original()), [REF], cloud_allowed=allowed)
        with self.assertRaises(ValueError):
            prepare_material(history(original()), ['c'*64], cloud_allowed=True)

    def test_original_immutable_deduplicated_no_model_prose_or_private_extra(self):
        value = original(); value['account'] = 'secret'; value['excerpts'][0]['token'] = 'secret'
        rows = history(value) + history(value, call='again') + [{'role':'assistant','content':'必然上涨'}]
        before = copy.deepcopy(rows)
        result = self.prepare(rows)
        self.assertEqual(result, self.prepare(rows))
        self.assertEqual(rows, before)
        task = result['packet']['tasks'][0]
        self.assertEqual(len(task['original_sources']), 1)
        self.assertNotIn('secret', str(result)); self.assertNotIn('必然上涨', str(result))

    def test_conflicting_snapshot_keeps_both_with_gap(self):
        old = original(); new = original(); new['excerpts'][0]['text'] = '修订文本'
        result = self.prepare(history(old) + history(new, call='new'))
        self.assertEqual(len(result['packet']['tasks'][0]['original_sources']), 2)
        self.assertEqual(result['diagnostics']['tasks'][0]['source_conflicts'], 1)

    def test_typed_financial_validation_legacy_text_retained(self):
        value = financial(); result = self.prepare(history(value))
        self.assertEqual(len(result['packet']['tasks'][0]['financial_fields']), 1)
        value['financial_fields'][0]['value_yi_yuan'] = '82.39'
        task = self.prepare(history(value))['packet']['tasks'][0]
        self.assertEqual(task['financial_fields'], [])
        self.assertEqual(task['source_field_text_not_new_binding'][0]['text'], value['excerpts'][0]['text'])

    def test_report_analysis_separate_latest_invalid_never_uses_old_body(self):
        value = comparison(); value['source_reference'] = REF
        rows = history(value, 'stock_report_read')
        task = self.prepare(rows)['packet']['tasks'][0]
        self.assertEqual(len(task['comparison_basis']['subjects']), 2)
        self.assertNotIn('旧过程页', str(task))
        bad = copy.deepcopy(value); bad.pop('answer_pages')
        task = self.prepare(rows + history(bad, 'stock_report_read', 'new'))['packet']['tasks'][0]
        self.assertIsNone(task['comparison_basis'])
        self.assertEqual(task['prior_analysis_not_source_fact'], [])

    def test_failed_unpaired_wrong_reference_and_legacy_not_authority(self):
        for mutate in ('failed', 'wrong', 'unpaired', 'legacy'):
            value = original(); rows = history(value)
            if mutate == 'failed': value['error'] = 'failed'
            if mutate == 'wrong': value['source_reference'] = 'c'*64
            if mutate == 'legacy': value.pop('source_reference')
            rows[1]['content'] = json.dumps(value)
            if mutate == 'unpaired': rows = rows[1:]
            with self.subTest(mutate=mutate), self.assertRaises(ValueError): self.prepare(rows)

    def test_explicit_multiple_tasks_never_merge_sources(self):
        second = original(); second['source_reference'] = 'c'*64
        result = prepare_material(history(original())+history(second,call='other'),
                                  [REF,'c'*64],cloud_allowed=True)
        self.assertEqual([t['reference'] for t in result['packet']['tasks']], [REF,'c'*64])
        self.assertEqual(len(self.prepare(history(original())+history(second,call='other'))['packet']['tasks']), 1)
