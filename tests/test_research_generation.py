"""Frozen specialist requests and ambiguous transport outcomes."""
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import httpx
from fastapi import HTTPException
from core.research_generation import generate, validate_public
from core.public_address import public_url
from core.providers import CallBudget
from pathlib import Path
import tempfile
import sqlite3
import json
from contextlib import closing


PARAMS = dict(enable_thinking=True, thinking_budget=32768, temperature=0.3, max_tokens=65536)


class DirectTests(unittest.IsolatedAsyncioTestCase):
    async def test_stop_associated_node_returns_unknown_to_stock_and_ids_never_leave(self):
        import asyncio
        from core.research_generation import stop_job
        entered=asyncio.Event()
        self.body['node']={'job_id':'cancel-test','request_id':'node-1'}
        async def pending(*args,**kwargs):
            self.assertNotIn('node',kwargs['json'])
            self.assertNotIn('cancel-test',json.dumps(kwargs['json']))
            entered.set()
            await asyncio.Event().wait()
        client=AsyncMock(); client.post.side_effect=pending
        manager=AsyncMock(); manager.__aenter__.return_value=client
        with patch('core.research_generation.read_key',return_value='test'), patch(
                'core.research_generation.httpx.AsyncClient',return_value=manager):
            task=asyncio.create_task(generate(self.runtime,self.body))
            await entered.wait()
            stopped=await stop_job('cancel-test')
            result=await task
        self.assertEqual(stopped['cancelled_nodes'],1)
        self.assertEqual(result['status'],'UNKNOWN')
        self.assertEqual(result['error'],'REQUEST_CANCELLED')
        with closing(sqlite3.connect(self.ledger.path)) as db:
            self.assertEqual(db.execute('SELECT status FROM research_receipts').fetchone()[0],'UNKNOWN')

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.ledger = CallBudget(Path(self.temp.name) / 'model-calls.sqlite', limit=1)
        self.body = dict(duty='C03', provider='qwen', model='qwen3.8-flash', generation=PARAMS,
            prompt='研究公开资料', execution_hash='a'*64,
            material=dict(as_of='2026-10-10', material={'evidence': {'text': '公开公告'}}, evidence_ids=['E1']))
        self.runtime = SimpleNamespace(models=SimpleNamespace(budget=self.ledger, catalog=AsyncMock(), resolve=lambda *a: (
            dict(model='qwen3.8-flash', kind='cloud', capabilities=['text'],
                 base_url='https://example.com/v1', api_key_env='TEST_KEY'), None)))

    async def call(self, response=None, error=None, stream_response=None):
        client = AsyncMock()
        client.post.return_value = response
        client.post.side_effect = error
        if stream_response is not None:
            stream_context = AsyncMock()
            stream_context.__aenter__.return_value = stream_response
            client.stream = Mock(return_value=stream_context)
        manager = AsyncMock()
        manager.__aenter__.return_value = client
        with patch('core.research_generation.read_key', return_value='test'), patch(
                'core.research_generation.httpx.AsyncClient', return_value=manager):
            result = await generate(self.runtime, self.body)
        return result, client

    async def test_frozen_stream_transport_reaches_wire_and_ledger(self):
        from tests.test_research_stream import response, chunk
        self.body['transport'] = {'stream': True, 'include_usage': True}
        stream = response([chunk('{}', 'stop'), {'choices': [], 'usage': {'total_tokens': 15}}])
        stream.status_code = 200
        stream.raise_for_status = lambda: None
        result, client = await self.call(stream_response=stream)
        self.assertEqual(result['status'], 'COMPLETE')
        self.assertEqual(client.post.call_count, 0)
        sent = client.stream.call_args.kwargs['json']
        self.assertTrue(sent['stream'])
        self.assertEqual(sent['stream_options'], {'include_usage': True})
        self.assertEqual(result['model_params']['stream_options'], sent['stream_options'])
        with closing(sqlite3.connect(self.ledger.path)) as db:
            row = db.execute('SELECT status,receipt FROM research_receipts WHERE call_id=?',
                             (result['ledger_call_id'],)).fetchone()
        self.assertEqual(row[0], 'COMPLETE')
        self.assertEqual(json.loads(row[1])['response_id'], 'response-1')

    async def test_unsupported_transport_is_not_silently_downgraded(self):
        self.body['transport'] = {'stream': True, 'include_usage': False}
        with self.assertRaises(HTTPException):
            await self.call()

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
        with closing(sqlite3.connect(self.ledger.path)) as db:
            saved = db.execute('SELECT status,receipt FROM research_receipts WHERE call_id=?',
                               (result['ledger_call_id'],)).fetchone()
        self.assertEqual(saved[0], 'COMPLETE')
        self.assertNotIn('text', json.loads(saved[1]))

    async def test_truncation_and_network_unknown_are_distinct(self):
        response = httpx.Response(200, request=httpx.Request('POST','https://example.com'), json={
            'model':'qwen3.8-flash', 'choices':[{'finish_reason':'length','message':{'content':'{'}}]})
        result, _ = await self.call(response)
        self.assertEqual(result['status'], 'TRUNCATED')
        result, client = await self.call(error=httpx.ReadTimeout('unknown'))
        self.assertEqual(result['status'], 'UNKNOWN')
        self.assertEqual(client.post.call_count, 1)
        self.assertEqual(self.ledger.status()['used'], 2)
        with closing(sqlite3.connect(self.ledger.path)) as db:
            self.assertEqual(db.execute('SELECT status FROM research_receipts WHERE call_id=?',
                (result['ledger_call_id'],)).fetchone()[0], 'UNKNOWN')

    async def test_unknown_diagnostics_are_fixed_categories_without_echo_or_retry(self):
        from tests.test_research_stream import response, chunk
        self.body['transport']={'stream':True,'include_usage':True}
        cases=[([chunk('{')],None,'STREAM_MISSING_TERMINAL'),
               ([chunk('{')],httpx.ReadTimeout('private-input-secret'),'TRANSPORT_TIMEOUT'),
               ([chunk('{')],httpx.RemoteProtocolError('private-input-secret'),'TRANSPORT_PROTOCOL'),
               ([{'error':{'message':'private-input-secret'}}],None,'STREAM_PROVIDER_ERROR'),
               ([chunk('{'),chunk('}', 'stop',id='other')],None,'STREAM_IDENTITY_MISMATCH'),
               (['private-input-secret'],None,'STREAM_INVALID_STRUCTURE')]
        for events,error,category in cases:
            with self.subTest(category=category):
                stream=response(events,error);stream.status_code=200;stream.raise_for_status=lambda:None
                result,client=await self.call(stream_response=stream)
                self.assertEqual(result['status'],'UNKNOWN')
                self.assertEqual(result['failure_category'],category)
                self.assertEqual(client.stream.call_count,1)
                with closing(sqlite3.connect(self.ledger.path)) as db:
                    saved=db.execute('SELECT receipt FROM research_receipts WHERE call_id=?',(result['ledger_call_id'],)).fetchone()[0]
                self.assertEqual(json.loads(saved)['failure_category'],category)
                self.assertNotIn('private-input-secret',str(result)+saved)

    async def test_existing_ledger_backed_up_before_incremental_table(self):
        self.ledger.reserve('legacy')
        await self.call(httpx.Response(400))
        backup = Path(self.ledger.path).with_name('model-calls.before-research.sqlite')
        with closing(sqlite3.connect(str(backup))) as db:
            self.assertEqual(db.execute('SELECT model FROM calls').fetchall(), [('legacy',)])
            self.assertIsNone(db.execute("SELECT name FROM sqlite_master WHERE name='research_receipts'").fetchone())

    async def test_failed_backup_cannot_be_mistaken_for_completed_migration(self):
        self.ledger.reserve('legacy')
        with patch('pathlib.Path.replace', side_effect=PermissionError('backup unavailable')):
            with self.assertRaises(PermissionError):
                await self.call(httpx.Response(400))
        backup = Path(self.ledger.path).with_name('model-calls.before-research.sqlite')
        self.assertFalse(backup.exists())
        with closing(sqlite3.connect(self.ledger.path)) as db:
            self.assertIsNone(db.execute("SELECT name FROM sqlite_master WHERE name='research_receipts'").fetchone())
        await self.call(httpx.Response(400))
        self.assertTrue(backup.exists())

    async def test_rejected_parameter_is_known_failure(self):
        result, client = await self.call(httpx.Response(400))
        self.assertEqual(result['status'], 'FAILED')
        self.assertEqual(client.post.call_count, 1)

    async def test_rejection_preserves_safe_diagnosis_without_provider_echo(self):
        self.body['transport'] = {'stream': True, 'include_usage': True}
        response = httpx.Response(400, json={'error': {'code': 'context_length_exceeded',
            'message': 'maximum context length; echoed private-secret-material'}})
        result, client = await self.call(stream_response=response)
        self.assertEqual(result['status'], 'FAILED')
        self.assertEqual(result['rejection_category'], 'CONTEXT_LENGTH_EXCEEDED')
        self.assertEqual(result['http_status'], 400)
        self.assertGreater(result['input_chars'], 0)
        self.assertEqual(client.stream.call_count, 1)
        with closing(sqlite3.connect(self.ledger.path)) as db:
            receipt = db.execute('SELECT receipt FROM research_receipts WHERE call_id=?',
                                 (result['ledger_call_id'],)).fetchone()[0]
        self.assertNotIn('private-secret-material', receipt + str(result))
        self.assertEqual(json.loads(receipt)['rejection_category'], 'CONTEXT_LENGTH_EXCEEDED')

    async def test_plain_400_does_not_invent_a_context_failure(self):
        result, _ = await self.call(httpx.Response(400, json={'error': {'message': 'invalid request'}}))
        self.assertEqual(result['rejection_category'], 'UNCLASSIFIED')
        self.assertEqual(result['status'], 'FAILED')

    async def test_dashscope_diagnostics_preserved_without_echo_or_resend(self):
        trace = '649b2bbc-c541-9e16-9845-db7fe4fe5b2d'
        cases = [
            ('Range of input length should be [1, 262144]', 'CONTEXT_LENGTH_EXCEEDED'),
            ('Range of max_tokens should be [1, 65536]', 'PARAMETER_OUT_OF_RANGE'),
            ('The thinking_budget parameter must be a positive integer and not greater than 32768', 'PARAMETER_OUT_OF_RANGE'),
            ('parameter.enable_thinking only support stream call', 'PARAMETER_COMBINATION_INVALID'),
            ('invalid request; echoed maximum context length private-secret', 'PARAMETER_INVALID'),
        ]
        for streaming in (False, True):
            if streaming:
                self.body['transport'] = {'stream': True, 'include_usage': True}
            for message, category in cases:
                with self.subTest(stream=streaming, category=category):
                    response = httpx.Response(400, json={'code': 'InvalidParameter',
                        'message': message + '; private-secret', 'request_id': trace})
                    result, client = await self.call(**({'stream_response': response} if streaming else {'response': response}))
                    self.assertEqual(result['status'], 'FAILED')
                    self.assertEqual(result['rejection_category'], category)
                    self.assertEqual(result['provider_error_code'], 'InvalidParameter')
                    self.assertEqual(result['provider_request_id'], trace)
                    sent = (client.stream if streaming else client.post).call_args.kwargs['json']
                    for key, value in PARAMS.items(): self.assertEqual(sent[key], value)
                    self.assertEqual((client.stream if streaming else client.post).call_count, 1)
                    with closing(sqlite3.connect(self.ledger.path)) as db:
                        saved = db.execute('SELECT receipt FROM research_receipts WHERE call_id=?',
                            (result['ledger_call_id'],)).fetchone()[0]
                    self.assertEqual(json.loads(saved)['provider_request_id'], trace)
                    self.assertNotIn('private-secret', str(result) + saved)

    async def test_unreadable_rejection_body_still_preserves_valid_header_trace(self):
        trace = '649b2bbc-c541-9e16-9845-db7fe4fe5b2d'
        result, client = await self.call(httpx.Response(400, text='private-secret', headers={'X-Request-ID': trace}))
        self.assertEqual(result['rejection_category'], 'UNCLASSIFIED')
        self.assertEqual(result['provider_request_id'], trace)
        self.assertEqual(client.post.call_count, 1)
        self.assertNotIn('private-secret', str(result))

    async def test_unknown_error_codes_and_invalid_or_conflicting_trace_not_stored(self):
        trace = '649b2bbc-c541-9e16-9845-db7fe4fe5b2d'
        for headers, expected in (({'X-Request-ID': 'secret-input'}, 'NOT_REPORTED'),
                ({'X-Request-ID': trace}, 'CONFLICT')):
            body = {'error': {'code': 'secret-input', 'message': 'invalid request'},
                    'request_id': 'aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa' if expected == 'CONFLICT' else 'secret-input'}
            result, _ = await self.call(httpx.Response(400, json=body, headers=headers))
            self.assertEqual(result['provider_request_id_status'], expected)
            self.assertNotIn('provider_request_id', result)
            self.assertNotIn('provider_error_code', result)
            self.assertNotIn('secret-input', str(result))

    async def test_fixed_content_rejection_and_http_auth_priority(self):
        result, _ = await self.call(httpx.Response(400, json={'error': {'code': 'DataInspectionFailed'}}))
        self.assertEqual(result['rejection_category'], 'CONTENT_REJECTED')
        result, _ = await self.call(httpx.Response(401, json={'error': {'code': 'InvalidParameter',
            'message': 'maximum context length'}}))
        self.assertEqual(result['rejection_category'], 'AUTHENTICATION')

    async def test_ledger_itself_drops_unvetted_diagnostic_fields(self):
        call_id = self.ledger.begin_research('qwen:qwen3.8-flash', 'a'*64, 'b'*64, PARAMS)
        self.ledger.finish_research(call_id, {'status': 'FAILED', 'provider_error_code': 'secret-input',
            'provider_request_id': 'secret-input', 'provider_request_id_status': 'secret-input', 'message': 'secret-input'})
        with closing(sqlite3.connect(self.ledger.path)) as db:
            receipt = db.execute('SELECT receipt FROM research_receipts WHERE call_id=?', (call_id,)).fetchone()[0]
        self.assertNotIn('secret-input', receipt)

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
