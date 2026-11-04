"""Offline native Ollama receipts; no model requests or format repair."""
import json
import unittest
import httpx
from core.llm_client import LLMClient


class LocalNativeResponseTests(unittest.IsolatedAsyncioTestCase):
    async def client(self, rows=(), status=200, raw=None, timeout=False):
        client = LLMClient('http://localhost:12434', 'fixture')
        await client._client.aclose()
        self.requests, self.observed = [], []
        class Body(httpx.AsyncByteStream):
            def __init__(self, content):
                self.content=content
            async def __aiter__(self):
                yield self.content
        def respond(request):
            self.requests.append(json.loads(request.content))
            if timeout:
                raise httpx.ReadTimeout('private-test-body', request=request)
            content = raw if raw is not None else '\n'.join(json.dumps(r) for r in rows)
            return httpx.Response(status, stream=Body(content.encode()))
        client._client = httpx.AsyncClient(transport=httpx.MockTransport(respond))
        self.addAsyncCleanup(client.close)
        return client

    async def run_client(self, client):
        return [r async for r in client.chat_stream_with_tools(
            [{'role':'user','content':'private-test-body'}],
            request_observer=lambda e,d:self.observed.append((e,d)))]

    def receipt(self):
        requests=[d for e,d in self.observed if e=='native_model_request']
        results=[d for e,d in self.observed if e=='native_model_result']
        self.assertEqual(len(requests),1)
        self.assertEqual(len(results),1)
        self.assertEqual(len(self.requests),1)
        self.assertEqual(requests[0]['local_attempt_id'],results[0]['local_attempt_id'])
        self.assertNotIn('private-test-body',json.dumps(self.observed))
        self.assertNotIn('hidden-test-reasoning',json.dumps(self.observed))
        return results[0]

    async def test_complete_records_usage_and_does_not_archive_thinking(self):
        client=await self.client([{'message':{'thinking':'hidden-test-reasoning','content':'ok'}},
            {'done':True,'done_reason':'stop','prompt_eval_count':123,'eval_count':17}])
        rows=await self.run_client(client)
        self.assertEqual(rows[-1][1]['content'],'ok')
        receipt=self.receipt()
        self.assertEqual(receipt['result_status'],'COMPLETE')
        self.assertEqual(receipt['usage'],{'prompt_tokens':123,'completion_tokens':17})
        self.assertIsNotNone(receipt['first_chunk_seconds'])
        self.assertGreaterEqual(receipt['last_chunk_seconds'],receipt['first_chunk_seconds'])

    async def test_http500_streaming_body_classified_without_saving_prose(self):
        client=await self.client(status=500, raw=json.dumps({'error':
            'llama runner process has terminated: private-test-body'}))
        with self.assertRaisesRegex(RuntimeError,'HTTP 500'):
            await self.run_client(client)
        receipt=self.receipt()
        self.assertEqual(receipt['http_status'],500)
        self.assertEqual(receipt['result_status'],'UNKNOWN')
        self.assertEqual(receipt['local_error_kind'],'RUNNER_TERMINATED')
        self.assertEqual(receipt['failure_stage'],'http_response')
        self.assertIsNone(receipt['usage'])

    async def test_length_is_not_a_completed_role_result(self):
        client=await self.client([{'message':{'content':'{"artifacts":['},'done':True,'done_reason':'length'}])
        with self.assertRaisesRegex(RuntimeError,'未完整'):
            await self.run_client(client)
        receipt=self.receipt()
        self.assertEqual(receipt['result_status'],'TRUNCATED')
        self.assertTrue(receipt['terminal_received'])

    async def test_bad_packet_is_not_skipped_before_good_terminal(self):
        client=await self.client(raw='INVALID\n'+json.dumps({'done':True,'message':{'content':'ok'}}))
        with self.assertRaisesRegex(RuntimeError,'无效 JSON'):
            await self.run_client(client)
        self.assertEqual(self.receipt()['result_status'],'UNKNOWN')

    async def test_eof_without_terminal_stays_unknown(self):
        client=await self.client([{'message':{'content':'partial'}}])
        with self.assertRaisesRegex(RuntimeError,'提前结束'):
            await self.run_client(client)
        self.assertEqual(self.receipt()['failure_category'],'EARLY_EOF')

    async def test_stream_error_is_not_empty_success(self):
        client=await self.client([{'error':'out of memory: private-test-body'}])
        with self.assertRaisesRegex(RuntimeError,'流返回错误'):
            await self.run_client(client)
        receipt=self.receipt()
        self.assertEqual(receipt['failure_category'],'STREAM_PROVIDER_ERROR')
        self.assertEqual(receipt['local_error_kind'],'MEMORY_REQUIRED')

    async def test_timeout_has_phase_and_no_zero_usage(self):
        client=await self.client(timeout=True)
        with self.assertRaisesRegex(RuntimeError,'超时'):
            await self.run_client(client)
        receipt=self.receipt()
        self.assertEqual(receipt['failure_stage'],'read')
        self.assertEqual(receipt['result_status'],'UNKNOWN')
        self.assertIsNone(receipt['usage'])

    async def test_close_without_terminal_does_not_retry(self):
        client=await self.client([{'message':{'content':'partial'}}])
        stream=client.chat_stream_with_tools([],request_observer=lambda e,d:self.observed.append((e,d)))
        await anext(stream)
        await stream.aclose()
        receipt=self.receipt()
        self.assertEqual(receipt['delivery_status'],'CANCELLED')
        self.assertEqual(receipt['result_status'],'UNKNOWN')
