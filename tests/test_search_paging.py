import importlib.util
import sys
import unittest
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch, AsyncMock

ROOT = Path(__file__).resolve().parents[1] / 'skills' / 'websearch'
sys.path.insert(0, str(ROOT))
from search_batches import select_batch, PageSnapshots, engine_capabilities


class SearchBatchTests(unittest.TestCase):
    def test_non_paging_engine_preserves_whole_first_page_without_fake_second_page(self):
        data = {'results': [{'url': f'https://example.com/{i}'} for i in range(25)],
                '_engine_capabilities': [{'name':'bing', 'paging':False, 'time_range_support':False}]}
        rows, first = select_batch(data, page=1, offset=0, size=12)
        self.assertEqual(first['pagination_support'], 'UNSUPPORTED')
        self.assertEqual(first['non_paging_engines'], ['bing'])
        cursor = first['next_cursor']
        while cursor:
            batch, receipt = select_batch(data, size=12, **cursor)
            rows.extend(batch)
            cursor = receipt['next_cursor']
        self.assertEqual(len(rows), 25)
        self.assertEqual(receipt['page'], 1)
        self.assertEqual(receipt['coverage'], 'provider_page_only')

    def test_mixed_capabilities_keep_next_page_and_unknown_is_not_supported(self):
        data = {'results':[{'url':'https://example.com/a'}], '_engine_capabilities':[
            {'name':'bing','paging':False}, {'name':'other','paging':True}]}
        _, receipt = select_batch(data, page=1, offset=0, size=12)
        self.assertEqual(receipt['pagination_support'], 'PARTIAL')
        self.assertEqual(receipt['next_cursor'], {'page':2,'offset':0})
        data['_engine_capabilities'] = None
        _, receipt = select_batch(data, page=1, offset=0, size=12)
        self.assertEqual(receipt['pagination_support'], 'UNKNOWN')

    def test_engine_config_is_typed_and_ignores_disabled_and_other_categories(self):
        bing = {'name':'bing','enabled':True,'categories':['general','web'],
                'paging':False,'time_range_support':False,'private':'must not copy'}
        config = {'engines':[bing, {**bing,'name':'disabled','enabled':False},
                             {**bing,'name':'images','categories':['images']}]}
        self.assertEqual(engine_capabilities(config),
                         [{'name':'bing','paging':False,'time_range_support':False}])
        for bad in (None, {}, {'engines':[{**bing,'paging':'false'}]},
                    {'engines':[{**bing,'categories':None}]}):
            self.assertIsNone(engine_capabilities(bad))

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
    async def test_startup_fetches_capabilities_and_snapshots_keep_them(self):
        spec = importlib.util.spec_from_file_location('search_service_capabilities', ROOT / 'app.py')
        service = importlib.util.module_from_spec(spec)
        with patch.dict(sys.modules, {'readability': SimpleNamespace(Document=object)}):
            spec.loader.exec_module(service)
        calls = []
        async def get(url, **kwargs):
            calls.append(url)
            data = ({'engines':[{'name':'bing','enabled':True,'categories':['general'],
                                'paging':False,'time_range_support':False}]} if url.endswith('/config') else
                    {'results':[{'title':str(i),'url':f'https://example.com/{i}'} for i in range(3)]})
            return SimpleNamespace(raise_for_status=lambda:None, json=lambda:data)
        client = SimpleNamespace(get=get, aclose=AsyncMock())
        with patch.object(service.httpx, 'AsyncClient', return_value=client):
            await service._startup()
        with tempfile.TemporaryDirectory() as root:
            service._page_snapshots = PageSnapshots(root)
            first = await service.web_search(service.WebSearchRequest(query='公开问题',max_results=2))
            service._engine_capabilities = None  # Continuation must retain the original page's scope.
            last = await service.web_search(service.WebSearchRequest(query='公开问题',**first['next_cursor']))
        self.assertEqual(len(calls),2)
        self.assertEqual(last['pagination_support'],'UNSUPPORTED')
        self.assertIsNone(last['next_cursor'])
        self.assertIn('不支持翻页',last['summary'])
        self.assertEqual(first['page_hash'],last['page_hash'])
        client.get = AsyncMock(side_effect=service.httpx.ConnectError('unavailable'))
        with patch.object(service.httpx,'AsyncClient',return_value=client):
            await service._startup()
        self.assertIsNone(service._engine_capabilities)
        await service._shutdown()

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
