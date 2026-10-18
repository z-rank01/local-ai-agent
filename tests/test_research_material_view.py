import copy
from hashlib import sha256
import json
from types import SimpleNamespace
import unittest

from core.research_material_view import ResearchMaterialView


class ResearchMaterialViewTests(unittest.TestCase):
    def prepare(self):
        saved = {}
        def archive(row):
            eid = sha256(json.dumps(row, sort_keys=True).encode()).hexdigest()
            saved[eid] = row
            return eid
        manager = SimpleNamespace(archive=archive, reader=lambda eid: saved[eid], evidence_ids=set())
        rows = [{'role': 'user', 'content': '原始问题与反证要求'}]
        for i in range(4):
            rows.extend([
                {'role': 'assistant', 'content': '保留分析', 'tool_calls': [
                    {'id': str(i), 'function': {'name': 'code_exec', 'arguments': {'code': 'print(1)'*1000}}}]},
                {'role': 'tool', 'tool_call_id': str(i), 'tool_name': 'code_exec', 'content': '事实\n'*2000+'末尾反证'}])
        return rows, manager, saved

    def test_complete_batch_readback_without_fake_executable_arguments(self):
        rows, manager, saved = self.prepare()
        before = copy.deepcopy(rows)
        index = ResearchMaterialView()
        view = index.apply(rows, manager)
        self.assertEqual(rows, before)
        self.assertEqual(view[0], rows[0])
        self.assertEqual(view[-4:], rows[-4:])
        for position, original in ((1, rows[1:3]), (2, rows[3:5])):
            self.assertNotIn('tool_calls', view[position])
            pointer = json.loads(view[position]['content'].split('历史工具资料：\n')[1])
            recovered = json.loads(manager.reader(pointer['evidence_id'])['content'])['messages']
            self.assertEqual(recovered, original)
            self.assertIn('末尾反证', pointer['results'][0]['preview_end'])
            self.assertIn(pointer['evidence_id'], manager.evidence_ids)
        self.assertEqual([row['tool_calls'] for row in view if row.get('tool_calls')],
                         [rows[5]['tool_calls'], rows[7]['tool_calls']])
        count = len(saved)
        self.assertEqual(index.apply(view, manager), view)
        self.assertEqual(index.apply(rows, manager), view)
        self.assertEqual(len(saved), count)

    def test_no_reader_or_failed_archive_keeps_whole_original_batch(self):
        rows, manager, _ = self.prepare()
        manager.reader = None
        self.assertEqual(ResearchMaterialView().apply(rows, manager), rows)
        manager.reader = lambda eid: None
        manager.archive = lambda row: None
        self.assertEqual(ResearchMaterialView().apply(rows, manager), rows)

    def test_small_results_remain_visible_and_incomplete_calls_are_untouched(self):
        rows, manager, _ = self.prepare()
        rows[2]['content'] = '计算结果100；错误明确保留'
        rows.insert(0, {'role': 'tool', 'tool_call_id': 'old', 'content': '旧轮原文'*1000})
        view = ResearchMaterialView().apply(rows, manager)
        self.assertEqual(view[0], rows[0])
        self.assertIn('计算结果100', view[2]['content'])
        rows[2]['tool_calls'][0]['id'] = 'unmatched'
        view = ResearchMaterialView().apply(rows, manager)
        self.assertEqual(view[2:4], rows[2:4])
