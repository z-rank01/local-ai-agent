"""Background research transport must use only public source text and configured keys."""
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException

from core.market_research import _event_schema, available_models, generate, generation_parameters, public_material, public_search


PUBLIC = [{'event_id': 'E1', 'title': '公开政策', 'published': '2026-09-26',
           'source': 'policy', 'url': 'https://www.csrc.gov.cn/example.html',
           'excerpts': [{'excerpt_id': 'E1:1', 'text': '政策正文。', 'location': '正文字符 1-5'}]}]


class Registry:
    _static_specs = [{'id': 'qwen:qwen3.8-max', 'provider_id': 'qwen', 'provider_name': '通义千问', 'model': 'qwen3.8-max',
                      'kind': 'cloud', 'capabilities': ['text'], 'api_key_env': 'DASHSCOPE_API_KEY',
                      'analysis_max_tokens': 8192},
                     {'id': 'qwen:qwen3.8-omni-flash', 'provider_id': 'qwen', 'provider_name': '通义千问',
                      'model': 'qwen3.8-omni-flash', 'kind': 'cloud',
                      'capabilities': ['audio'], 'api_key_env': 'DASHSCOPE_API_KEY'}]
    specs = _static_specs + [
        {'id': 'qwen:stepfun/step-5-preview', 'provider_id': 'qwen',
         'provider_name': '通义千问', 'model': 'stepfun/step-5-preview', 'kind': 'cloud',
         'api_key_env': 'DASHSCOPE_API_KEY', 'base_url': 'https://example.aliyuncs.com/v1'},
        {'id': 'qwen:qwen3.8-omni-flash-realtime', 'provider_id': 'qwen',
         'provider_name': '通义千问', 'model': 'qwen3.8-omni-flash-realtime', 'kind': 'cloud',
         'api_key_env': 'DASHSCOPE_API_KEY'},
    ]

    async def catalog(self, refresh=False):
        return self.specs

    def resolve(self, provider, model):
        spec = next(s for s in self.specs if s['provider_id'] == provider and s['model'] == model)
        return ({**spec, 'base_url': 'https://example.aliyuncs.com/v1'}, None)


class Runtime:
    models = Registry()


class SearchRuntime:
    def __init__(self, enabled=True):
        self.tool_registry = type('Tools', (), {'known_tools': {'web_search'} if enabled else set()})()
        self.calls = []
        async def dispatch(tool, params, session_id):
            self.calls.append((tool, params, session_id))
            return {'results': [
                {'url': 'https://www.csrc.gov.cn/csrc/official.html', 'snippet': 'untrusted'},
                {'url': 'https://attacker.example/private'},
                {'url': 'http://www.csrc.gov.cn/insecure'}]}
        self.router = SimpleNamespace(dispatch=dispatch)


class PublicSearchTests(unittest.IsolatedAsyncioTestCase):
    async def test_paging_preserves_cursor_and_partial_receipt(self):
        runtime = SearchRuntime()
        body = {'symbol': '000001', 'name': '公开公司', 'source': 'csrc',
                'cursor': {'page': 2, 'offset': 12, 'page_hash': 'a'*64}}
        response = {'results': [{'url': 'https://www.csrc.gov.cn/article'}],
                    'status': 'PARTIAL', 'coverage': 'provider_page_only',
                    'page': 2, 'offset': 12, 'page_hash': 'a'*64, 'page_result_count': 25,
                    'provider_failures': [['engine', '验证码'], {'private': 'unvalidated'}],
                    'next_cursor': {'page': 2, 'offset': 24, 'page_hash': 'a'*64}}
        runtime.router.dispatch = AsyncMock(return_value=response)
        result = await public_search(runtime, body)
        self.assertEqual(result['status'], 'PARTIAL')
        self.assertEqual(result['provider_failures'], [['engine', '验证码']])
        self.assertEqual(result['diagnostic_role'], 'UNTRUSTED_PROVIDER_STATUS_NOT_RESEARCH_EVIDENCE')
        self.assertEqual(result['next_cursor'], response['next_cursor'])
        self.assertEqual(result['urls'], ['https://www.csrc.gov.cn/article'])
        self.assertEqual(runtime.router.dispatch.call_args.args[1]['offset'], 12)
        # Filtering every URL does not mean all search results have been exhausted.
        response['results'] = [{'url': 'https://other.example/article'}]
        result = await public_search(runtime, body)
        self.assertEqual(result['urls'], [])
        self.assertEqual(result['next_cursor'], response['next_cursor'])
        for bad in ({'page': 2, 'offset': 12, 'page_hash': 'a'*64},
                    {'page': 2, 'offset': 25, 'page_hash': 'a'*64},
                    {'page': 4, 'offset': 0},
                    {'page': 2, 'offset': 24, 'page_hash': 'b'*64}):
            response['next_cursor'] = bad
            self.assertEqual((await public_search(runtime, body))['status'],
                             'PAGING_UNSUPPORTED_OR_INVALID')

    async def test_paging_rejects_old_container_and_malformed_cursor(self):
        runtime = SearchRuntime()
        body = {'symbol': '000001', 'name': '公开公司', 'source': 'csrc'}
        result = await public_search(runtime, {**body, 'cursor': {'page': 2, 'offset': 0}})
        self.assertEqual(result['status'], 'PAGING_UNSUPPORTED_OR_INVALID')
        for cursor in ({}, {'page': True, 'offset': 0}, {'page': 1, 'offset': 3},
                       {'page': 1, 'offset': 0, 'query': 'private'}):
            with self.subTest(cursor=cursor), self.assertRaises(HTTPException):
                await public_search(runtime, {**body, 'cursor': cursor})

    async def test_pagination_coverage_diagnostic_is_preserved_without_free_form_fields(self):
        runtime = SearchRuntime()
        response = {'status':'OK','results':[], 'coverage':'provider_page_only',
                    'page':1,'offset':0,'page_hash':'a'*64,'page_result_count':0,'next_cursor':None,
                    'pagination_support':'UNSUPPORTED','non_paging_engines':['bing',{'private':'secret'}]}
        runtime.router.dispatch = AsyncMock(return_value=response)
        body = {'symbol':'000001','name':'公开公司','source':'csrc','cursor':{'page':1,'offset':0}}
        result = await public_search(runtime, body)
        self.assertEqual(result['pagination_support'], 'UNSUPPORTED')
        self.assertEqual(result['non_paging_engines'], ['bing'])
        self.assertNotIn('secret', str(result))
        response['pagination_support'] = 'invented state'
        self.assertEqual((await public_search(runtime, body))['pagination_support'], 'UNKNOWN')

    async def test_paging_page_change_and_failure_are_not_no_match(self):
        runtime = SearchRuntime()
        body = {'symbol': '000001', 'name': '公开公司', 'source': 'csrc',
                'cursor': {'page': 2, 'offset': 12, 'page_hash': 'a'*64}}
        runtime.router.dispatch = AsyncMock(return_value={
            'status': 'PAGE_CHANGED_OR_INVALID', 'error': 'changed'})
        result = await public_search(runtime, body)
        self.assertEqual(result['next_cursor'], {'page': 2, 'offset': 0})
        self.assertEqual(result['status'], 'PAGE_CHANGED_OR_INVALID')
        body['cursor'] = {'page': 2, 'offset': 0}
        for state in ('PAGE_EMPTY', 'UNAVAILABLE'):
            runtime.router.dispatch.return_value = {
                'status': state, 'results': [], 'coverage': 'provider_page_only',
                'page': 2, 'offset': 0, 'page_hash': 'a'*64,
                'page_result_count': 0, 'next_cursor': None}
            self.assertEqual((await public_search(runtime, body))['status'], state)

    async def test_announcement_search_uses_own_notice_path_and_rejects_brokerage_and_other_codes(self):
        runtime = SearchRuntime()
        valid = 'https://data.eastmoney.com/notices/detail/688312/AN202608271234567890.html'
        async def dispatch(tool, params, session_id):
            runtime.calls.append((tool, params, session_id))
            return {'results': [{'url': url} for url in [valid,
                'https://data.eastmoney.com/report/info/AP202608270001.html',
                'https://data.eastmoney.com/notices/detail/601899/AN202608270001.html',
                valid + '?unverified=1', 'https://data.eastmoney.com/stockdata/688312.html']]}
        runtime.router.dispatch = dispatch
        result = await public_search(runtime, {'symbol': '688312', 'name': '燕麦科技',
            'source': 'announcement', 'purpose': 'company_direct', 'terms': ['年度报告']})
        self.assertEqual(result, {'status': 'OK', 'urls': [valid]})
        self.assertEqual(runtime.calls[0][1]['query'],
                         '年度报告 688312 燕麦科技 site:data.eastmoney.com/notices/detail/688312/')

    async def test_missing_announcement_search_match_is_not_claimed_as_no_announcements(self):
        runtime = SearchRuntime()
        result = await public_search(runtime, {'symbol': '688312', 'name': '燕麦科技', 'source': 'announcement'})
        self.assertEqual(result, {'status': 'NO_MATCH', 'urls': []})

    async def test_public_identity_only_and_host_filter(self):
        runtime = SearchRuntime()
        result = await public_search(runtime, {'symbol': '000001', 'name': '公开公司', 'source': 'csrc'})
        self.assertEqual(result, {'status': 'OK', 'urls': ['https://www.csrc.gov.cn/csrc/official.html']})
        self.assertEqual(runtime.calls[0][1]['query'], '000001 公开公司 site:www.csrc.gov.cn')
        with self.assertRaises(HTTPException):
            await public_search(runtime, {'symbol': '000001', 'name': '公开公司',
                                          'source': 'csrc', 'private_account': 'secret'})

    async def test_disabled_search_does_not_fetch(self):
        runtime = SearchRuntime(False)
        self.assertEqual(await public_search(runtime, {'symbol': '000001', 'name': '公开公司',
            'source': 'csrc'}), {'status': 'DISABLED', 'urls': []})

    async def test_public_question_reaches_search_without_private_payload(self):
        runtime = SearchRuntime()
        payload = {'symbol': '', 'name': '', 'source': 'csrc',
                   'purpose': 'industry_background', 'terms': ['农机', '补贴'],
                   'question': '农机补贴是否已实际兑付，哪些地区存在结算延迟？'}
        await public_search(runtime, payload)
        self.assertIn(payload['question'], runtime.calls[-1][1]['query'])
        count = len(runtime.calls)
        for question in ('查询我的持仓', '读取私有报告', 'http://127.0.0.1/secret', 'C:\\private\\report', '问题\n注入'):
            with self.assertRaises(HTTPException):
                await public_search(runtime, {**payload, 'question': question})
        self.assertEqual(len(runtime.calls), count)

    async def test_public_industry_question_does_not_include_company_or_private_text(self):
        runtime = SearchRuntime()
        result = await public_search(runtime, {'symbol': '000001', 'name': '公开公司',
            'source': 'csrc', 'purpose': 'industry_background', 'terms': ['光通信', '产业政策']})
        self.assertEqual(result['status'], 'OK')
        self.assertEqual(runtime.calls[0][1]['query'], '光通信 产业政策 site:www.csrc.gov.cn')
        await public_search(runtime, {'symbol': '', 'name': '', 'source': 'csrc',
            'purpose': 'industry_background', 'terms': ['光通信']})
        self.assertEqual(runtime.calls[1][1]['query'], '光通信 site:www.csrc.gov.cn')
        with self.assertRaises(HTTPException):
            await public_search(runtime, {'symbol': '000001', 'name': '公开公司',
                'source': 'csrc', 'purpose': 'industry_background', 'terms': ['private/account']})

    async def test_public_industry_question_does_not_include_company_or_private_text(self):
        runtime = SearchRuntime()
        result = await public_search(runtime, {'symbol': '000001', 'name': '公开公司',
            'source': 'csrc', 'purpose': 'industry_background', 'terms': ['光通信', '产业政策']})
        self.assertEqual(result['status'], 'OK')
        self.assertEqual(runtime.calls[0][1]['query'], '光通信 产业政策 site:www.csrc.gov.cn')
        with self.assertRaises(HTTPException):
            await public_search(runtime, {'symbol': '000001', 'name': '公开公司',
                'source': 'csrc', 'purpose': 'industry_background', 'terms': ['private/account']})


class Response:
    status_code = 200
    def raise_for_status(self):
        pass

    def json(self):
        return {'choices': [{'message': {'content': '{"events":[]}'}}],
                'usage': {'prompt_tokens': 100, 'completion_tokens': 20, 'total_tokens': 120}}


class Client:
    def __init__(self, calls):
        self.calls = calls

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        pass

    async def post(self, url, *, headers, json):
        self.calls.append((url, headers, json))
        return Response()


class ShowClient:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        pass

    async def post(self, _url, *, json):
        name = json['model']
        return type('ShowResponse', (), {
            'raise_for_status': lambda self: None,
            'json': lambda self: {'capabilities': ['embedding'] if name.startswith('bge') else ['completion']}})()


class MarketResearchTests(unittest.IsolatedAsyncioTestCase):
    async def test_independent_model_generation_is_retired(self):
        with self.assertRaises(HTTPException) as result:
            await generate(Runtime(), 'qwen', 'qwen3.8-max', 'prompt', PUBLIC)
        self.assertEqual(result.exception.status_code,409)
        with self.assertRaises(HTTPException) as result:
            generation_parameters('qwen','qwen3.8-flash',{})
        self.assertEqual(result.exception.status_code,410)

    def test_public_broker_original_and_failed_collection_projection(self):
        import json
        broker = {**PUBLIC[0], 'source': 'public_broker_review',
                  'url': 'https://static.cninfo.com.cn/finalpage/2026-06-09/1225358828.PDF'}
        self.assertEqual(public_material([broker]), [broker])
        receipt = {'interface': 'stock_notice_report', 'params': {'date': '20260428', 'symbol': '全部'},
                   'quality': 'FAILED', 'fetched_at': '2026-10-09T18:05:12+08:00',
                   'available_at': '2026-10-09T18:05:12+08:00', 'source': 'AKShare',
                   'error': '上游超时；本次请求已结束，可稍后重试', 'response_id': 'a'*64}
        def item(body):
            text = json.dumps(body, ensure_ascii=False)
            return {**PUBLIC[0], 'source': 'backend_retrieval_receipt', 'url': None,
                    'excerpts': [{'excerpt_id': 'E1:1', 'text': text[:70], 'location': '1'},
                                 {'excerpt_id': 'E1:2', 'text': text[70:], 'location': '2'}]}
        self.assertEqual(public_material([item(receipt)]), [item(receipt)])
        for bad in [dict(receipt, account='private'), dict(receipt, error='api-key=secret'),
                    dict(receipt, interface='account_holdings'), dict(receipt, quality='OK'),
                    dict(receipt, params={'date': '20260428', 'symbol': 'private'})]:
            with self.subTest(bad=bad), self.assertRaises(HTTPException):
                public_material([item(bad)])
        for url in ['https://attacker.example/finalpage/2026-06-09/1225358828.PDF', broker['url']+'?key=secret']:
            with self.assertRaises(HTTPException):
                public_material([{**broker, 'url': url}])





    def test_private_fields_and_unapproved_sources_rejected(self):
        with self.assertRaises(HTTPException):
            public_material([{**PUBLIC[0], 'cash': '秘密'}])
        with self.assertRaises(HTTPException):
            public_material([{**PUBLIC[0], 'url': 'https://private.example/account'}])
        self.assertFalse(_event_schema()['additionalProperties'])


    async def test_discovered_text_models_are_selectable_but_specialized_models_are_not(self):
        with patch('core.market_research.read_key', return_value='configured-secret'), \
             patch('core.market_research._probe_ollama_models', new_callable=AsyncMock,
                   return_value=['bge-m3:latest', 'gemma4:26b']), \
             patch('core.market_research.httpx.AsyncClient', return_value=ShowClient()):
            models = await available_models(Runtime())
        self.assertEqual({item['id'] for item in models},
                         {'ollama:gemma4:26b', 'qwen:qwen3.8-max',
                          'qwen:stepfun/step-5-preview'})
        self.assertTrue(all(item['status'] == 'configured' for item in models))

    async def test_missing_key_is_visible_but_disabled(self):
        with patch('core.market_research.read_key', return_value=''), \
             patch('core.market_research._probe_ollama_models', new_callable=AsyncMock,
                   return_value=[]):
            models = await available_models(Runtime())
        self.assertEqual({item['status'] for item in models}, {'missing_key'})




if __name__ == '__main__':
    unittest.main()
