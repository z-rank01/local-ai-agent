"""Background research transport must use only public source text and configured keys."""
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException

from core.market_research import _event_schema, available_models, generate, public_material, public_search


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


class Response:
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
