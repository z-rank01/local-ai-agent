import copy
from hashlib import sha256
import json
from types import SimpleNamespace
import unittest

from core.research_material_view import ResearchMaterialView


class ResearchMaterialViewTests(unittest.TestCase):
    def prepare(self):
        originals = {}
        def archive(row):
            eid = sha256(json.dumps(row, sort_keys=True).encode()).hexdigest()
            originals[eid] = row
            return eid
        manager = SimpleNamespace(archive=archive, reader=lambda eid: originals[eid], evidence_ids=set())
        rows = [{'role': 'user', 'content': '原始问题与反证要求'}]
        for i in range(4):
            rows.extend([
                {'role': 'assistant', 'content': '保留分析', 'tool_calls': [
                    {'id': str(i), 'function': {'name': 'code_exec', 'arguments': {'code': 'print(1)'*1000}}}]},
                {'role': 'tool', 'tool_call_id': str(i), 'tool_name': 'code_exec', 'content': '事实\n'*2000+'末尾反证'}])
        return rows, manager, originals

    def test_large_old_bodies_and_arguments_are_recoverable_and_idempotent(self):
        rows, manager, originals = self.prepare()
        before = copy.deepcopy(rows)
        index = ResearchMaterialView()
        view = index.apply(rows, manager)
        self.assertEqual(rows, before)
        self.assertEqual(view[0], rows[0])
        self.assertEqual(view[-4:], rows[-4:])
        pointer = json.loads(view[2]['content'])
        self.assertEqual(manager.reader(pointer['evidence_id'])['content'], rows[2]['content'])
        self.assertIn('末尾反证', pointer['preview_end'])
        self.assertIn(pointer['evidence_id'], manager.evidence_ids)
        self.assertLess(len(json.dumps(view)), len(json.dumps(rows)))
        count = len(originals)
        self.assertEqual(index.apply(view, manager), view)
        self.assertEqual(index.apply(rows, manager), view)
        self.assertEqual(len(originals), count)

    def test_no_reader_or_failed_archive_keeps_original(self):
        rows, manager, _ = self.prepare()
        manager.reader = None
        self.assertEqual(ResearchMaterialView().apply(rows, manager), rows)
        manager.reader = lambda eid: None
        manager.archive = lambda row: None
        self.assertEqual(ResearchMaterialView().apply(rows, manager), rows)

    def test_previous_turn_and_small_results_are_not_indexed(self):
        rows, manager, _ = self.prepare()
        rows.insert(0, {'role': 'tool', 'tool_call_id': 'old', 'content': '旧轮原文'*1000})
        rows[3]['content'] = '计算结果100；错误明确保留'
        view = ResearchMaterialView().apply(rows, manager)
        self.assertEqual(view[0], rows[0])
        self.assertEqual(view[3], rows[3])
