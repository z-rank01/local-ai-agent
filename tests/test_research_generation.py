"""Frozen specialist requests and ambiguous transport outcomes."""
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx
from fastapi import HTTPException
from core.research_generation import generate, validate_public
from core.public_address import public_url


PARAMS = dict(enable_thinking=True, thinking_budget=32768, temperature=0.3, max_tokens=65536)


class DirectTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.body = dict(duty='C03', provider='qwen', model='qwen3.8-flash', generation=PARAMS,
            prompt='研究公开资料', execution_hash='a'*64,
            material=dict(as_of='2026-10-10', material={'evidence': {'text': '公开公告'}}, evidence_ids=['E1']))
        self.runtime = SimpleNamespace(models=SimpleNamespace(catalog=AsyncMock(), resolve=lambda *a: (
            dict(model='qwen3.8-flash', kind='cloud', capabilities=['text'],
                 base_url='https://example.com/v1', api_key_env='TEST_KEY'), None)))

    async def call(self, response=None, error=None):
        client = AsyncMock()
        client.post.return_value = response
        client.post.side_effect = error
        manager = AsyncMock()
        manager.__aenter__.return_value = client
        with patch('core.research_generation.read_key', return_value='test'), patch(
                'core.research_generation.httpx.AsyncClient', return_value=manager):
            result = await generate(self.runtime, self.body)
        return result, client

    async def test_frozen_parameters_and_no_hidden_reasoning(self):
        response = httpx.Response(200, request=httpx.Request('POST','https://example.com'), json={
            'id':'r1', 'model':'qwen3.8-flash', 'choices':[{'finish_reason':'stop',
            'message':{'content':'{}', 'reasoning_content':'do not store'}}], 'usage':{'total_tokens':45}})
        result, client = await self.call(response)
        self.assertEqual(result['status'], 'COMPLETE')
        payload = client.post.call_args.kwargs['json']
        for key, value in PARAMS.items():
            self.assertEqual(payload[key], value)
            self.assertEqual(result['model_params'][key], value)
        self.assertNotIn('do not store', str(result))

    async def test_truncation_and_network_unknown_are_distinct(self):
        response = httpx.Response(200, request=httpx.Request('POST','https://example.com'), json={
            'model':'qwen3.8-flash', 'choices':[{'finish_reason':'length','message':{'content':'{'}}]})
        result, _ = await self.call(response)
        self.assertEqual(result['status'], 'TRUNCATED')
        result, client = await self.call(error=httpx.ReadTimeout('unknown'))
        self.assertEqual(result['status'], 'UNKNOWN')
        self.assertEqual(client.post.call_count, 1)

    async def test_rejected_parameter_is_known_failure(self):
        result, client = await self.call(httpx.Response(400))
        self.assertEqual(result['status'], 'FAILED')
        self.assertEqual(client.post.call_count, 1)

    async def test_private_material_rejected_before_network(self):
        self.body['material']['material']['holdings'] = ['private']
        with self.assertRaises(HTTPException):
            await self.call()

    async def test_invalid_parameter_not_silently_removed(self):
        self.body['generation'] = {**PARAMS, 'unsupported': 1}
        with self.assertRaises(HTTPException):
            await self.call()


class PublicAddressTests(unittest.TestCase):
    def test_public_company_cash_is_not_account_cash(self):
        from copy import deepcopy
        financial = {'symbol': '688312', 'snapshot_id': 'a'*64,
                     'latest': {'ytd': {'cash': 12.5}}, 'periods': [{'ytd': {'cash': 10}}]}
        packet = {'material': {'instrument': {'symbol': '688312'},
            'evidence': {'financial': financial}, 'metrics': {'financial': {'ytd': {'cash': 12.5}}},
            'peers': [{'symbol': '688312', 'financial': deepcopy(financial)}]}}
        validate_public(packet)
        for location in ('root', 'nested', 'wrong_symbol', 'text', 'holdings'):
            changed = deepcopy(packet)
            if location == 'root': changed['material']['cash'] = 999
            elif location == 'nested': changed['material']['evidence']['cash'] = 999
            elif location == 'wrong_symbol': changed['material']['instrument']['symbol'] = '000001'
            elif location == 'text': changed['material']['evidence']['financial']['latest']['ytd']['cash'] = 'private text'
            else: changed['material']['evidence']['financial']['latest']['ytd']['holdings'] = []
            with self.subTest(location=location), self.assertRaises(HTTPException):
                validate_public(changed)

    def test_local_and_credential_urls_rejected(self):
        for url in ['https://127.0.0.1/x','https://[::1]/x','https://localhost/x',
                    'https://host.internal/x','https://u:p@example.com/x','http://example.com/x',
                    'https://example.com:9510/x','https://10.0.0.1/x']:
            with self.subTest(url=url), self.assertRaises(ValueError):
                public_url(url)
        self.assertEqual(public_url('https://example.com/news').hostname, 'example.com')
