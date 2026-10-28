import json
import tempfile
import unittest
from pathlib import Path

from scripts.harness_content_review import (build_sheet, iter_cases, read_review,
    review_path_for, write_review)


def make_evidence(directory: Path, role='local', variant='v9', case='meeting-notes-m1'):
    case_dir = directory / role / variant
    case_dir.mkdir(parents=True)
    evidence = {
        'key': case, 'variant': variant, 'capability': 'meeting-notes', 'task': 'm1',
        'model': 'test:model', 'status': 'STREAM_COMPLETE',
        'prompt': '请整理 record.md', 'run': {'status': 'SUCCEEDED', 'error': None},
        'checks': {'tools': ['file_read'], 'required_tools_present': True},
        'tool_calls': [{'tool_name': 'file_read', 'params': {'path': '/workspace/record.md'},
                        'status': 'ok', 'content': '原始记录内容'}],
        'assistant': ['', '整理结果：已决定事项一。'],
    }
    (case_dir / (case + '.json')).write_text(json.dumps(evidence, ensure_ascii=False), encoding='utf-8')
    return evidence


class ContentReviewTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_sheet_contains_prompt_tool_trajectory_and_final_answer(self):
        evidence = make_evidence(self.root)
        sheet = build_sheet(evidence)
        self.assertIn('请整理 record.md', sheet)
        self.assertIn('file_read', sheet)
        self.assertIn('整理结果：已决定事项一。', sheet)
        self.assertIn('run: SUCCEEDED', sheet)

    def test_write_review_creates_status_file_next_to_evidence(self):
        make_evidence(self.root, role='cloud', variant='v17')
        path = write_review(self.root, 'cloud', 'v17', 'meeting-notes-m1', 'COMPLETED',
                            ['一处日期核对存疑'], '人工复核通过')
        self.assertEqual(path, review_path_for(self.root, 'cloud', 'v17', 'meeting-notes-m1'))
        record = json.loads(path.read_text(encoding='utf-8'))
        self.assertEqual(record['status'], 'COMPLETED')
        self.assertEqual(record['case'], 'cloud/v17/meeting-notes-m1')
        self.assertEqual(read_review(self.root, 'cloud', 'v17', 'meeting-notes-m1')['status'], 'COMPLETED')

    def test_write_review_rejects_unknown_status_and_missing_evidence(self):
        make_evidence(self.root)
        with self.assertRaises(ValueError):
            write_review(self.root, 'local', 'v9', 'meeting-notes-m1', 'MAYBE', [], '')
        with self.assertRaises(SystemExit):
            write_review(self.root, 'local', 'v9', 'table-reconcile-t1', 'COMPLETED', [], '')

    def test_iter_cases_skips_registry_and_review_files(self):
        make_evidence(self.root)
        variant_dir = self.root / 'local' / 'v9'
        (variant_dir / 'runs.json').write_text('{}', encoding='utf-8')
        (variant_dir / 'case.json').write_text('{}', encoding='utf-8')
        (variant_dir / 'inputs.json').write_text('{}', encoding='utf-8')
        write_review(self.root, 'local', 'v9', 'meeting-notes-m1', 'FAILED', [], '')
        cases = list(iter_cases(self.root, 'local', 'v9'))
        self.assertEqual(cases, [('local', 'v9', 'meeting-notes-m1')])


if __name__ == '__main__':
    unittest.main()
