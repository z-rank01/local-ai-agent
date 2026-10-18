import importlib.util
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, ROOT / path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


source = load('source_document', 'skills/websearch/source_document.py')
math = load('research_math', 'skills/runner/packages/harness-stock-research-v1/scripts/calculate.py')


class ResearchSourcesTests(unittest.TestCase):
    def test_pagination_and_snapshot_conflict(self):
        args = dict(url='https://example.org/report.pdf', title='Report',
                    pages=[{'page': 1, 'text': 'abc'}, {'page': 2, 'text': 'def'}])
        first = source.source_snapshot(b'original', max_chars=10, include_file=True, **args)
        rest = source.source_snapshot(b'original', offset=first['next_offset'],
                                      expected_hash=first['sha256'], **args)
        full = source.source_snapshot(b'original', **args)
        self.assertEqual(first['content'] + rest['content'], full['content'])
        self.assertNotIn('_source_file', rest)
        self.assertEqual(first['_source_file']['pages'], args['pages'])
        with self.assertRaisesRegex(ValueError, '来源已变化'):
            source.source_snapshot(b'changed', expected_hash=first['sha256'], **args)

    def test_mean_does_not_assume_even_revenue_recognition(self):
        result = math.calculate({'operation': 'divide', 'unit': '元/年', 'inputs': [
            {'value': '114001200', 'unit': '元', 'period': '合同期限', 'source': 'E1'},
            {'value': '3', 'unit': '年', 'period': '合同期限', 'source': 'E1'}]})
        self.assertEqual(result['result'], '38000400')
        with self.assertRaises(ValueError):
            math.calculate({**result, 'operation': 'sum'})

    def test_nonfinite_rejected(self):
        with self.assertRaises(ValueError):
            math.calculate({'operation': 'sum', 'inputs': [
                {'value': 'NaN', 'unit': '元', 'period': '2025', 'source': 'E1'}]})
