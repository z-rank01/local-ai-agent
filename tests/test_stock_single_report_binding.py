import copy
import hashlib
import json
import unittest
from types import SimpleNamespace

from plugins.finance.source_quotes import prior_explanation_view, source_task_index, original_records
from plugins.finance.report_context import report_material_view
from plugins.finance.task_source_view import lookup_index
from plugins.finance.reply_purpose import selection_view
from tests.test_stock_explanation_view import source_turn
from tests.test_stock_report_context import report
from tests.test_stock_source_quotes import SourceQuoteLoopTests

REF = 'a' * 64
QUESTION = '请核对这份保存报告的期间和单位，保留反证。'


def history(question=QUESTION):
    return ([{'role': 'system', 'content': '原研究方法和权限'}] + source_turn(REF, '甲')
            + report(REF, 'legacy', '完整旧阶段与反证', '旧版正文')
            + report(REF, 'latest', '最新阶段与反证', '最新部分成果正文')
            + [{'role': 'assistant', 'content': '读报告后的旧解释'},
               {'role': 'user', 'content': question}])


class SingleReportBindingTests(unittest.TestCase):
    def setUp(self):
        self.saved = {}

    def archive(self, row):
        eid = hashlib.sha256(json.dumps(row, sort_keys=True).encode()).hexdigest()
        self.saved[eid] = copy.deepcopy(row)
        return eid

    def test_natural_question_indexes_losslessly_before_summary(self):
        rows = history(); before = copy.deepcopy(rows)
        view, priors = prior_explanation_view(rows, self.archive)
        view, materials = report_material_view(view, self.archive)
        self.assertTrue(priors); self.assertEqual(len(materials), 2)
        self.assertEqual(rows, before); self.assertEqual(view[-1], rows[-1])
        self.assertEqual(original_records(view), original_records(rows))
        self.assertEqual(source_task_index(view), source_task_index(rows))
        self.assertEqual([r for r in view if r['role'] == 'user'], [r for r in rows if r['role'] == 'user'])
        self.assertEqual([r['tool_calls'] for r in view if r.get('tool_calls')],
                         [r['tool_calls'] for r in rows if r.get('tool_calls')])
        for entry in priors + materials:
            archived = self.saved[entry['evidence_id']]
            self.assertIn({k: v for k, v in archived.items() if k != 'record_kind'}, rows)
        latest = json.loads(next(r['content'] for r in view if r.get('tool_call_id') == 'latest'))['model_observation']
        self.assertEqual(latest['answer_pages'][0]['body'], '最新部分成果正文')

    def test_unknown_explicit_reference_does_not_fall_back(self):
        rows = history('未知引用 ' + 'd' * 64)
        self.assertEqual(prior_explanation_view(rows, self.archive), (rows, []))
        self.assertEqual(report_material_view(rows, self.archive), (rows, []))
        lookup = lookup_index(source_task_index(rows), rows[-1]['content'])
        self.assertEqual(lookup['selection'], 'explicit_partial'); self.assertEqual(lookup['rows'], [])

    def test_multiple_tasks_are_ambiguous_even_with_only_one_report(self):
        rows = history(); rows[-1:-1] = source_turn('b' * 64, '乙')
        self.assertEqual(prior_explanation_view(rows, self.archive), (rows, []))
        self.assertEqual(report_material_view(rows, self.archive), (rows, []))
        self.assertEqual(lookup_index(source_task_index(rows), QUESTION)['selection'], 'all_known')

    def test_multiple_explicit_reports_keep_both(self):
        rows = history(REF + ' ' + 'b' * 64)
        rows[-1:-1] = report('b' * 64, 'second', '其他阶段反证', '其他正文')
        _, material = report_material_view(rows, self.archive)
        self.assertEqual({r['source_reference'] for r in material}, {REF, 'b' * 64})

    def test_source_only_does_not_become_report_binding(self):
        rows = source_turn(REF, '甲') + [{'role': 'user', 'content': QUESTION}]
        self.assertEqual(prior_explanation_view(rows, self.archive), (rows, []))
        self.assertEqual(lookup_index(source_task_index(rows), QUESTION)['selection'], 'all_known')

    def test_report_without_raw_source_cannot_index_old_explanation(self):
        rows = [{'role': 'user', 'content': REF}] + report(REF, 'r', '阶段', '正文')
        rows += [{'role': 'assistant', 'content': '旧结论'}, {'role': 'user', 'content': QUESTION}]
        self.assertEqual(prior_explanation_view(rows, self.archive), (rows, []))

    def test_bad_report_receipts_cannot_select_singleton(self):
        for mode in ('outer_failed', 'inner_failed', 'error', 'mismatch', 'unpaired', 'wrong_tool', 'empty_id'):
            with self.subTest(mode=mode):
                rows = report(REF, 'r', '旧', '新')
                envelope = json.loads(rows[1]['content'])
                if mode == 'outer_failed': envelope['status'] = 'FAILED'
                if mode == 'inner_failed': envelope['model_observation']['status'] = 'RULE_BLOCKED'
                if mode == 'error': envelope['model_observation']['error'] = 'RULE_BLOCKED'
                if mode == 'mismatch': envelope['model_observation']['source_reference'] = 'b' * 64
                if mode == 'unpaired': rows[1]['tool_call_id'] = 'other'
                if mode == 'wrong_tool': rows[0]['tool_calls'][0]['function']['name'] = 'stock_public_evidence_read'
                if mode == 'empty_id': rows[0]['tool_calls'][0]['id'] = rows[1]['tool_call_id'] = ''
                rows[1]['content'] = json.dumps(envelope)
                self.assertEqual(source_task_index(rows), [])

    def test_legacy_success_without_reference_remains_bound_to_its_call(self):
        rows = report(REF, 'r', '旧', '新')
        envelope = json.loads(rows[1]['content']); envelope['model_observation'].pop('source_reference')
        rows[1]['content'] = json.dumps(envelope)
        self.assertEqual(lookup_index(source_task_index(rows), QUESTION)['selection'], 'single_report_receipt')

    def test_no_current_body_or_archive_does_not_discard_report(self):
        rows = report(REF, 'r', '旧') + [{'role': 'user', 'content': QUESTION}]
        self.assertEqual(report_material_view(rows, self.archive), (rows, []))
        rows = history()
        self.assertEqual(report_material_view(rows, None), (rows, []))
        self.assertEqual(prior_explanation_view(rows, None), (rows, []))
        before = copy.deepcopy(rows)
        def fail(row): raise OSError('unavailable')
        with self.assertRaises(OSError): prior_explanation_view(rows, fail)
        self.assertEqual(rows, before)

    def test_singleton_purpose_uses_original_question_and_read_tools(self):
        rows = history(); index = source_task_index(rows); lookup = lookup_index(index, QUESTION)
        self.assertEqual(lookup['selection'], 'single_report_receipt')
        selected, tools = selection_view(rows, QUESTION, lookup,
            [{'function': {'name': name}} for name in ('stock_report_read', 'stock_research_submit')])
        self.assertEqual(selected[-1], rows[-1]); self.assertEqual(selected[0], rows[0])
        self.assertEqual([t['function']['name'] for t in tools], ['stock_report_read'])


class SingleReportAgentTests(unittest.IsolatedAsyncioTestCase):
    async def test_archive_precedes_context_processing_and_analysis_keeps_sources(self):
        captured = []; audits = []; processed = []; saved = {}
        class Model:
            cloud = False
            observes_native_requests = True
            async def chat_stream_with_tools(self, messages, tools, **kw):
                captured.append(copy.deepcopy(messages))
                kw['request_observer']('native_model_request', {'parameters': {'enable_thinking': True}})
                yield '', {'role': 'assistant', 'content': '{"reply_focus":"analysis"}' if len(captured) == 1 else '保留条件与部分成果。'}
        a = SourceQuoteLoopTests.agent(self, Model(), SimpleNamespace(), ['stock_report_read', 'stock_public_evidence_read'])
        a.turn_reply_purpose_enabled = True
        def archive(row):
            eid = hashlib.sha256(json.dumps(row, sort_keys=True).encode()).hexdigest(); saved[eid] = row; return eid
        async def process(rows): processed.append(copy.deepcopy(rows)); return rows
        a.context_mgr = SimpleNamespace(archive=archive, reader=object(), evidence_ids=set(), process=process)
        a.audit.record = lambda e, d: audits.append((e, d))
        rows = history(); before = copy.deepcopy(rows)
        events = [e async for e in a.run(rows)]
        self.assertEqual(events[-1].text, '保留条件与部分成果。')
        self.assertEqual(rows[1:], before[1:])
        self.assertTrue(rows[0]['content'].startswith(before[0]['content']))
        self.assertTrue(saved); self.assertNotIn('读报告后的旧解释', json.dumps(processed[0], ensure_ascii=False))
        self.assertEqual(captured[0][-1], rows[-1]); self.assertEqual(len(captured[0]), 3)
        self.assertEqual(original_records(captured[1]), original_records(rows))
        metadata = [d for e, d in audits if e == 'task_explanation_view'][0]
        self.assertEqual(metadata['task_material_binding_selection'], 'single_report_receipt')
        self.assertEqual(metadata['task_material_binding_count'], 1)
        native = [d for e, d in audits if e == 'native_model_request']
        self.assertEqual([d['reply_purpose_phase'] for d in native], ['selection', 'explanation'])
        self.assertTrue(all(d['source_lookup_selection'] == 'single_report_receipt' for d in native))

    async def test_grant_answer_only_and_archive_failure_preserve_original_history(self):
        for mode in ('no_grant', 'answer_only', 'archive_failure'):
            with self.subTest(mode=mode):
                captured = []
                class Model:
                    cloud = mode == 'no_grant'
                    async def chat_stream_with_tools(self, messages, tools, **kw):
                        captured.append(copy.deepcopy(messages))
                        yield '', {'role': 'assistant', 'content': '原回答路径。'}
                a = SourceQuoteLoopTests.agent(self, Model(), SimpleNamespace(), ['stock_report_read'])
                a.turn_reply_purpose_enabled = False
                async def process(rows): return rows
                def fail(row): raise OSError('unavailable')
                a.context_mgr = SimpleNamespace(archive=fail, reader=object(), evidence_ids=set(), process=process)
                if mode == 'no_grant': a.workspace_cloud_allowed = False
                if mode == 'answer_only': a.allow_tools = False
                rows = history(); before = copy.deepcopy(rows)
                events = [e async for e in a.run(rows)]
                self.assertEqual(rows[1:], before[1:]); self.assertEqual(events[-1].text, '原回答路径。')
                self.assertIn('读报告后的旧解释', json.dumps(captured[0], ensure_ascii=False))
