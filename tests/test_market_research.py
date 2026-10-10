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

    def test_explicit_generation_rejects_uncontrolled_or_invalid_parameters(self):
        valid = dict(enable_thinking=True, thinking_budget=32768, temperature=0.3, max_tokens=65536)
        self.assertEqual(generation_parameters('qwen', 'qwen3.8-flash', valid), valid)
        invalid = [dict(valid, enable_thinking=False), dict(valid, thinking_budget=True),
                   dict(valid, thinking_budget=65536), dict(valid, max_tokens=131073),
                   dict(valid, temperature=float('nan')), dict(valid, temperature=True),
                   dict(valid, reasoning_effort='high'), dict(valid, enable_search=True), {}]
        for value in invalid:
            with self.subTest(value=value), self.assertRaises(HTTPException):
                generation_parameters('qwen', 'qwen3.8-flash', value)
        with self.assertRaises(HTTPException):
            generation_parameters('qwen', 'qwen3.8-max', valid)

    async def test_explicit_thinking_profile_sent_and_frozen_in_receipt(self):
        calls = []
        options = dict(enable_thinking=True, thinking_budget=32768, temperature=0.3, max_tokens=65536)
        runtime = Runtime()
        spec = dict(Registry._static_specs[0], model='qwen3.8-flash', base_url='https://example.aliyuncs.com/v1')
        with patch.object(runtime.models, 'resolve', return_value=(spec, None)), \
             patch('core.market_research.read_key', return_value='configured-secret'), \
             patch('core.market_research.httpx.AsyncClient', return_value=Client(calls)), \
             patch.object(Response, 'json', return_value={
                 'id': 'public-response-id', 'model': 'qwen3.8-flash',
                 'choices': [{'message': {'content': '{"events":[]}', 'reasoning_content': 'private reasoning'},
                              'finish_reason': 'stop'}], 'usage': {'total_tokens': 123}}):
            result = await generate(runtime, 'qwen', 'qwen3.8-flash', '只输出 JSON', PUBLIC, generation=options)
        payload = calls[0][2]
        self.assertTrue(all(payload[k] == v for k, v in options.items()))
        self.assertFalse(payload['enable_search'])
        self.assertEqual(payload['response_format']['type'], 'json_schema')
        self.assertTrue(all(result['model_params'][k] == v for k, v in options.items()))
        self.assertEqual(result['response_model'], 'qwen3.8-flash')
        self.assertNotIn('private reasoning', str(result))
        self.assertEqual(len(calls), 1)

    async def test_explicit_profile_requires_terminal_receipt(self):
        calls = []
        spec = dict(Registry._static_specs[0], model='qwen3.8-flash', base_url='https://example.aliyuncs.com/v1')
        with patch.object(Runtime.models, 'resolve', return_value=(spec, None)), \
             patch('core.market_research.read_key', return_value='configured-secret'), \
             patch('core.market_research.httpx.AsyncClient', return_value=Client(calls)):
            result = await generate(Runtime(), 'qwen', 'qwen3.8-flash', '只输出 JSON', PUBLIC,
                                    generation=dict(enable_thinking=True, thinking_budget=32768, temperature=0.3, max_tokens=65536))
        self.assertEqual(result['status'], 'UNKNOWN')
        self.assertEqual(len(calls), 1)

    async def test_explicit_rejection_is_known_failure_and_never_retried(self):
        calls = []
        spec = dict(Registry._static_specs[0], model='qwen3.8-flash', base_url='https://example.aliyuncs.com/v1')
        with patch.object(Runtime.models, 'resolve', return_value=(spec, None)), \
             patch('core.market_research.read_key', return_value='configured-secret'), \
             patch('core.market_research.httpx.AsyncClient', return_value=Client(calls)), \
             patch.object(Response, 'status_code', 422):
            result = await generate(Runtime(), 'qwen', 'qwen3.8-flash', '只输出 JSON', PUBLIC,
                                    generation=dict(enable_thinking=True, thinking_budget=32768, temperature=0.3, max_tokens=65536))
        self.assertEqual(result['status'], 'FAILED')
        self.assertEqual(result['error'], 'PROVIDER_REJECTED')
        self.assertEqual(result['model_params']['thinking_budget'], 32768)
        self.assertEqual(len(calls), 1)

    def test_private_fields_and_unapproved_sources_rejected(self):
        with self.assertRaises(HTTPException):
            public_material([{**PUBLIC[0], 'cash': '秘密'}])
        with self.assertRaises(HTTPException):
            public_material([{**PUBLIC[0], 'url': 'https://private.example/account'}])
        self.assertFalse(_event_schema()['additionalProperties'])

    async def test_qwen_schema_uses_existing_key_and_no_chat_model_switch(self):
        calls = []
        with patch('core.market_research.read_key', return_value='configured-secret'), \
             patch('core.market_research.httpx.AsyncClient', return_value=Client(calls)):
            result = await generate(Runtime(), 'qwen', 'qwen3.8-max', '只输出 JSON', PUBLIC)
        self.assertEqual(result['usage']['total_tokens'], 120)
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0][2]['response_format']['type'], 'json_schema')
        self.assertEqual(calls[0][2]['model'], 'qwen3.8-max')
        self.assertEqual(calls[0][2]['max_tokens'], 8192)
        self.assertEqual(calls[0][1]['Authorization'], 'Bearer configured-secret')
        self.assertNotIn('cash', str(calls[0][2]))

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

    async def test_discovered_cloud_model_uses_prompt_json_without_unsupported_format(self):
        calls = []
        with patch('core.market_research.read_key', return_value='configured-secret'), \
             patch('core.market_research.httpx.AsyncClient', return_value=Client(calls)):
            result = await generate(Runtime(), 'qwen', 'stepfun/step-5-preview', '只输出 JSON', PUBLIC)
        self.assertEqual(result['usage']['total_tokens'], 120)
        self.assertEqual(calls[0][2]['model'], 'stepfun/step-5-preview')
        self.assertNotIn('max_tokens', calls[0][2])
        self.assertNotIn('response_format', calls[0][2])

    async def test_provider_length_finish_preserves_partial_output_for_continuation(self):
        calls = []
        with patch('core.market_research.read_key', return_value='configured-secret'), \
             patch('core.market_research.httpx.AsyncClient', return_value=Client(calls)), \
             patch.object(Response, 'json', return_value={
                 'choices': [{'message': {'content': '{"events":'}, 'finish_reason': 'length'}]}):
            result = await generate(Runtime(), 'qwen', 'qwen3.8-max', '只输出 JSON', PUBLIC)
        self.assertEqual(result['status'], 'TRUNCATED')
        self.assertEqual(result['text'], '{"events":')
        self.assertEqual(result['model_params']['max_tokens'], 8192)


if __name__ == '__main__':
    unittest.main()
