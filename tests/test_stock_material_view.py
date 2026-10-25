import copy
import hashlib
import json
import unittest

from core.stock_material_view import project_materials
from plugins.finance.report_context import report_material_view
from tests import test_stock_report_context as reports
from tests import test_financial_field_catalogue as catalogues


class MaterialViewTests(unittest.TestCase):
    def test_latest_directory_is_lossless_and_idempotent(self):
        ref = 'a' * 64
        context = catalogues.context()
        context['subjects'][1]['field_catalogue'] = catalogues.directory()
        rows = reports.report(ref, 'latest', '旧页', '同版正文', comparison_context=context)
        rows.append({'role': 'user', 'content': ref})
        original = copy.deepcopy(rows); saved = {}

        def archive(row):
            eid = hashlib.sha256(json.dumps(row, sort_keys=True).encode()).hexdigest()
            saved[eid] = copy.deepcopy(row)
            return eid

        view, index = report_material_view(rows, archive, compact_catalogues=True)
        obs = json.loads(view[1]['content'])['model_observation']
        subject = obs['comparison_context']['subjects'][1]
        self.assertNotIn('field_catalogue', subject)
        pointer = subject['field_catalogue_index']
        archived = json.loads(saved[pointer['evidence_id']]['content'])['model_observation']
        self.assertEqual(archived['comparison_context'], context)
        self.assertEqual(subject['metrics'], context['subjects'][1]['metrics'])
        self.assertEqual(pointer['field_count'], len(context['subjects'][1]['field_catalogue']['fields']))
        self.assertEqual(rows, original)
        again, extra = report_material_view(view, archive, compact_catalogues=True)
        self.assertEqual(again, view); self.assertFalse(extra)
        self.assertEqual(len(saved), 1)

    def test_unavailable_archive_retains_material(self):
        rows = [{'role': 'user', 'content': '问题'}]
        self.assertEqual(project_materials(rows, None).messages, rows)

    def test_source_review_transitions_do_not_change_parameters(self):
        from plugins.finance.source_review import SourceReview, Phase
        state = SourceReview(); state.begin('核对原文')
        self.assertEqual(state.phase, Phase.FIND)
        event = {'status': 'ok', 'name': 'stock_public_evidence_find', 'params': {'search': '原查询'},
                 'result': {'kind': 'public_evidence_find', 'items': []}}
        original = copy.deepcopy(event); state.received(event)
        self.assertEqual(state.phase, Phase.FIND); self.assertEqual(event, original)
        event['result']['items'] = [{'event_id': '1'}]; state.received(event)
        self.assertEqual(state.phase, Phase.READ)
        state.received({'status': 'ok', 'name': 'stock_public_evidence_read', 'result': {'next_cursor': None}})
        self.assertEqual(state.phase, Phase.EXPLANATION); self.assertEqual(state.attempts, 1)

    def test_duplicate_source_requires_exact_snapshot_and_preserves_current_turn(self):
        from core.stock_material_view import duplicate_source_view
        from tests.test_stock_receipts import rows
        first = rows(); second = copy.deepcopy(first)
        second[0]['tool_calls'][0]['id'] = 'read2'; second[1]['tool_call_id'] = 'read2'
        history = first + second + [{'role':'user','content':'当前问题'}] + rows()
        saved = {}
        def archive(row):
            eid = hashlib.sha256(json.dumps(row,sort_keys=True).encode()).hexdigest()
            saved[eid] = copy.deepcopy(row); return eid
        view,index = duplicate_source_view(history,archive)
        self.assertEqual(len(index),1)
        self.assertEqual(view[-2:],history[-2:])
        self.assertEqual(view[3],history[3])
        self.assertEqual(saved[index[0]['evidence_id']]['content'],first[1]['content'])
        again,extra = duplicate_source_view(view,archive)
        self.assertEqual(again,view); self.assertEqual(extra,[])
        changed = copy.deepcopy(history)
        obs = json.loads(changed[3]['content']); obs['model_observation']['next_cursor']='different'
        changed[3]['content']=json.dumps(obs)
        untouched,index = duplicate_source_view(changed,archive)
        self.assertEqual(untouched,changed); self.assertEqual(index,[])
