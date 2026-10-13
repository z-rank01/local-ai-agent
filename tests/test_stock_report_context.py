import copy
import hashlib
import json
import unittest

from core.stock_report_context import report_material_view
from core.stock_source_quotes import original_records, source_task_index
from tests.test_stock_explanation_view import source_turn


def report(ref, call, body, answer=None, **extra):
    value = {'kind': 'local_report_page', 'source_reference': ref, 'body': body,
             'page_count': 6, 'requested_page': 2, 'research_status': 'PARTIAL', **extra}
    if answer is not None:
        value['answer_pages'] = [{'kind': 'local_report_page', 'body': answer,
                                 'page_count': 1, 'requested_page': 1}]
    return [{'role': 'assistant', 'content': '', 'tool_calls': [{'id': call,
             'function': {'name': 'stock_report_read', 'arguments': json.dumps({'reference': ref})}}]},
            {'role': 'tool', 'tool_name': 'stock_report_read', 'tool_call_id': call,
             'content': json.dumps({'model_observation': value}, ensure_ascii=False)}]


class ReportMaterialViewTests(unittest.TestCase):
    def setUp(self):
        self.saved = {}
        self.ref = 'a' * 64

    def archive(self, row):
        eid = hashlib.sha256(json.dumps(row, sort_keys=True).encode()).hexdigest()
        self.saved[eid] = row
        return eid

    def test_current_body_and_whole_original_sources_survive_legacy_index(self):
        messages = ([{'role': 'system', 'content': '实际方法与权限'}] + source_turn(self.ref, '甲')
                    + report(self.ref, 'old', '旧阶段推断：资金已收回', '旧正文')
                    + report(self.ref, 'new', '执行记录及完整阶段反证', '当前部分成果，尚未完成综合')
                    + [{'role': 'user', 'content': '解释 ' + self.ref}])
        snapshot = copy.deepcopy(messages)
        view, index = report_material_view(messages, self.archive)
        self.assertEqual(messages, snapshot)
        self.assertEqual(original_records(view), original_records(messages))
        self.assertEqual(source_task_index(view), source_task_index(messages))
        self.assertEqual(view[0], messages[0]); self.assertEqual(view[-1], messages[-1])
        self.assertEqual(len(index), 2)
        old, new = [json.loads(m['content'])['model_observation'] for m in view if m.get('tool_name') == 'stock_report_read']
        self.assertNotIn('body', old); self.assertNotIn('answer_pages', old)
        self.assertNotIn('body', new)
        self.assertEqual(new['answer_pages'], json.loads(messages[-2]['content'])['model_observation']['answer_pages'])
        self.assertEqual(new['research_status'], 'PARTIAL')
        self.assertIn('完整阶段反证', self.saved[index[-1]['evidence_id']]['content'])
        self.assertIn('旧正文', self.saved[index[0]['evidence_id']]['content'])

    def test_no_valid_current_body_no_archive_or_change(self):
        for fields in ({}, {'answer_pages': []}, {'answer_pages': [{'kind': 'local_report_page', 'body': 'incomplete'}]},
                       {'status': 'RULE_BLOCKED', 'answer_pages': [{'kind': 'local_report_page', 'body': 'x', 'page_count': 1, 'requested_page': 1}]}):
            rows = report(self.ref, 'x', '唯一旧成果', **fields) + [{'role': 'user', 'content': self.ref}]
            self.assertEqual(report_material_view(rows, self.archive), (rows, []))
        self.assertFalse(self.saved)

    def test_other_task_current_source_and_receipt_parameters_never_rewritten(self):
        rows = report('b' * 64, 'other', '其他任务正文', '其他新版') + report(self.ref, 'this', '本任务旧页', '本任务新版')
        rows += source_turn(self.ref, '当前原文') + [{'role': 'user', 'content': self.ref}]
        view, _ = report_material_view(rows, self.archive)
        self.assertEqual(view[:2], rows[:2])
        self.assertEqual(view[-5:], rows[-5:])
        self.assertEqual(view[2], rows[2])

    def test_unknown_unpaired_or_mismatched_reference_does_not_index(self):
        rows = report(self.ref, 'x', '旧', '新') + [{'role': 'user', 'content': 'd' * 64}]
        self.assertEqual(report_material_view(rows, self.archive), (rows, []))
        rows[-1]['content'] = self.ref
        rows[1]['tool_call_id'] = 'wrong'
        self.assertEqual(report_material_view(rows, self.archive), (rows, []))
        rows[1]['tool_call_id'] = 'x'
        val = json.loads(rows[1]['content']); val['model_observation']['source_reference'] = 'b' * 64
        rows[1]['content'] = json.dumps(val)
        self.assertEqual(report_material_view(rows, self.archive), (rows, []))
        self.assertFalse(self.saved)

    def test_archive_failure_leaves_full_history_and_evidence_intact(self):
        rows = report(self.ref, 'x', '原阶段全部材料', '新版') + [{'role': 'user', 'content': self.ref}]
        before = copy.deepcopy(rows)
        def fail(row): raise OSError('archive failed')
        with self.assertRaises(OSError): report_material_view(rows, fail)
        self.assertEqual(rows, before)
        self.assertEqual(report_material_view(rows, None), (rows, []))

    def test_repeated_view_is_stable_new_receipt_moves_old_body_to_index(self):
        rows = report(self.ref, 'x', '旧材料', '先前新版') + [{'role': 'user', 'content': self.ref}]
        view, _ = report_material_view(rows, self.archive)
        again, index = report_material_view(view, self.archive)
        self.assertEqual(view, again); self.assertFalse(index)
        view += report(self.ref, 'y', '新材料', '更新后的新版')
        newer, index = report_material_view(view, self.archive)
        observations = [json.loads(m['content'])['model_observation'] for m in newer if m.get('tool_name') == 'stock_report_read']
        self.assertNotIn('answer_pages', observations[0])
        self.assertEqual(observations[1]['answer_pages'][0]['body'], '更新后的新版')
        self.assertEqual(len(self.saved), 2); self.assertEqual(len(index), 2)
