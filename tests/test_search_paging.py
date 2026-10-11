import importlib.util
import sys
import unittest
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1] / 'skills' / 'websearch'
sys.path.insert(0, str(ROOT))
from search_batches import select_batch, PageSnapshots


class SearchBatchTests(unittest.TestCase):
    def test_snapshot_scopes_query_and_survives_reader_restart(self):
        with tempfile.TemporaryDirectory() as root:
            cache = PageSnapshots(root)
            params = {'q': '公开政策', 'pageno': 1}
            data = {'results': [{'url': 'https://example.com/a'}]}
            _, meta = select_batch(data, page=1, offset=0, size=1)
            cache.save(params, meta['page_hash'], data)
            self.assertEqual(PageSnapshots(root).read(params, meta['page_hash']), data)
            for changed in ({**params, 'q': '另一问题'}, {**params, 'pageno': 2}):
                with self.assertRaises(ValueError):
                    cache.read(changed, meta['page_hash'])
            with self.assertRaises(ValueError):
                cache.read(params, '../outside')

    def test_more_than_twenty_results_are_reachable_without_gaps(self):
        pages = {1: [{'url': f'https://example.com/{i}'} for i in range(37)],
                 2: [{'url': f'https://example.com/{i}'} for i in range(37, 60)], 3: []}
        cursor = {'page': 1, 'offset': 0}; seen = []
        while cursor:
            rows, meta = select_batch({'results': pages[cursor['page']]}, size=12, **cursor)
            seen.extend(row['url'] for row in rows)
            cursor = meta['next_cursor']
        self.assertEqual(seen, [f'https://example.com/{i}' for i in range(60)])
        self.assertEqual(meta['status'], 'PAGE_EMPTY')
        self.assertEqual(meta['coverage'], 'provider_page_only')

    def test_changed_page_cannot_silently_skip_results(self):
        rows = [{'url': f'https://example.com/{i}'} for i in range(5)]
        _, meta = select_batch({'results': rows}, page=1, offset=0, size=2)
        with self.assertRaisesRegex(ValueError, '页已变化'):
            select_batch({'results': rows[::-1]}, size=2, **meta['next_cursor'])

    def test_engine_failure_is_not_an_empty_search(self):
        _, meta = select_batch({'results': [], 'unresponsive_engines': [['engine', 'timeout']]},
                              page=2, offset=0, size=12)
        self.assertEqual(meta['status'], 'UNAVAILABLE')
        with self.assertRaises(ValueError):
            select_batch({'results': None}, page=1, offset=0, size=12)


class SearchHandlerTests(unittest.IsolatedAsyncioTestCase):
    @unittest.skipUnless(importlib.util.find_spec('bs4'), 'HTTP handler needs the websearch container dependencies')
    async def test_handler_uses_cursor_and_preserves_long_snippet(self):
        spec = importlib.util.spec_from_file_location('search_service_paging', ROOT / 'app.py')
        service = importlib.util.module_from_spec(spec)
        # Readability is unrelated to search; the HTTP handler and Pydantic schema are real.
        with patch.dict(sys.modules, {'readability': SimpleNamespace(Document=object)}):
            spec.loader.exec_module(service)
        rows = [{'title': str(i), 'url': f'https://example.com/{i}',
                 'content': '原文线索' * 100 + '末尾反证'} for i in range(25)]
        calls = []
        async def get(url, **kwargs):
            calls.append(kwargs['params'])
            return SimpleNamespace(raise_for_status=lambda: None, json=lambda: {'results': rows})
        service._client = SimpleNamespace(get=get)
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        service._page_snapshots = PageSnapshots(tmp.name)
        first = await service.web_search(service.WebSearchRequest(query='公开问题', max_results=20, page=2))
        original_rows = list(rows)
        rows.reverse()  # Provider ordering changes; continuation must not fetch it again.
        second = await service.web_search(service.WebSearchRequest(query='公开问题', max_results=20,
                                                                  **first['next_cursor']))
        self.assertEqual([c['pageno'] for c in calls], [2])
        self.assertEqual(len(first['results']) + len(second['results']), 25)
        self.assertTrue(first['results'][0]['snippet'].endswith('末尾反证'))
        self.assertEqual(second['next_cursor'], {'page': 3, 'offset': 0})
        self.assertEqual([r['url'] for r in first['results'] + second['results']],
                         [r['url'] for r in original_rows])
        unavailable = await service.web_search(service.WebSearchRequest(query='不同问题', **first['next_cursor']))
        self.assertEqual(unavailable['status'], 'PAGE_CHANGED_OR_INVALID')
        self.assertEqual(len(calls), 1)
