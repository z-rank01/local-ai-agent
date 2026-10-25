import asyncio
import json
import unittest
from unittest.mock import patch

import httpx
from core.providers import CompatibleClient, CallBudget

RID='12345678-1234-1234-1234-123456789abc'
RESPONSE_ID='chatcmpl-1234567890abcdef'

class NativeResponseTests(unittest.IsolatedAsyncioTestCase):
    async def client(self, chunks=(), status=200, error=None, headers=None, raw=None, timeout=False):
        self.requests=[]
        client=CompatibleClient({'id':'qwen:mock','model':'mock','api_key_env':'IN1_FAKE_KEY',
            'base_url':'https://example.invalid/v1','options':{'enable_thinking':True}},CallBudget('unused',0))
        await client._client.aclose()
        def handler(request):
            self.requests.append(json.loads(request.content))
            if timeout: raise httpx.ReadTimeout('not-safe-request-body',request=request)
            if error is not None: return httpx.Response(status,json=error,headers=headers)
            text=raw if raw is not None else ''.join('data: '+json.dumps(x)+'\n\n' for x in chunks)+'data: [DONE]\n\n'
            return httpx.Response(status,text=text,headers=headers)
        client._client=httpx.AsyncClient(transport=httpx.MockTransport(handler))
        self.addAsyncCleanup(client.close)
        self.observed=[]
        return client

    async def run_client(self,client):
        with patch.dict('os.environ',{'IN1_FAKE_KEY':'dummy-secret'}):
            return [row async for row in client.chat_stream_with_tools([{'role':'user','content':'private-test-body'}],
                request_observer=lambda event,data:self.observed.append((event,data)))]

    def receipt(self):
        found=[data for event,data in self.observed if event=='native_model_result']
        self.assertEqual(len(found),1,'native terminal metadata missing or duplicated')
        requests=[data for event,data in self.observed if event=='native_model_request']
        self.assertEqual(len(requests),1)
        self.assertEqual(found[0]['local_attempt_id'],requests[0]['local_attempt_id'])
        self.assertNotIn('private-test-body',json.dumps(self.observed))
        self.assertNotIn('dummy-secret',json.dumps(self.observed))
        self.assertNotIn('hidden-test-reasoning',json.dumps(self.observed))
        self.assertEqual(len(self.requests),1)
        self.assertNotIn('stream_options',self.requests[0])
        self.assertNotIn('local_attempt_id',self.requests[0])
        self.assertTrue(self.requests[0]['enable_thinking'])
        return found[0]

    async def test_complete_result_records_provider_finish_id_usage_without_body(self):
        client=await self.client([{'id':RESPONSE_ID,'choices':[{'delta':{'reasoning_content':'hidden-test-reasoning','content':'answer'},'finish_reason':'stop'}]},
            {'choices':[],'usage':{'prompt_tokens':20,'completion_tokens':8,'total_tokens':28,
                'completion_tokens_details':{'reasoning_tokens':3,'unsafe':'private-test-body'},'unsafe':'private-test-body'}}],headers={'x-request-id':RID})
        result=await self.run_client(client)
        receipt=self.receipt()
        self.assertEqual(result[-1][1]['content'],'answer')
        self.assertEqual(receipt['result_status'],'COMPLETE')
        self.assertEqual(receipt['delivery_status'],'COMPLETE')
        self.assertEqual(receipt['finish_reason'],'stop')
        self.assertEqual(receipt['response_id'],RESPONSE_ID)
        self.assertEqual(receipt['provider_request_id'],RID)
        self.assertEqual(receipt['usage']['completion_tokens_details'],{'reasoning_tokens':3})
        self.assertEqual(receipt['usage_status'],'REPORTED')

    async def test_explicit_length_preserves_truncated_finish_without_retry(self):
        client=await self.client([{'id':RESPONSE_ID,'choices':[{'delta':{'content':'partial'},'finish_reason':'length'}]}])
        with self.assertRaisesRegex(RuntimeError,'未完整'): await self.run_client(client)
        receipt=self.receipt()
        self.assertEqual(receipt['result_status'],'TRUNCATED')
        self.assertEqual(receipt['finish_reason'],'length')
        self.assertIsNone(receipt['usage'])
        self.assertEqual(receipt['usage_status'],'NOT_REPORTED')

    async def test_textual_tool_protocol_does_not_complete_delivery_or_retry(self):
        client = await self.client([{'id': RESPONSE_ID, 'choices': [{'delta': {
            'content': '<｜｜DSML｜｜ calls>\n<｜｜DSML｜｜ invoke name="code_exec">'},
            'finish_reason': 'stop'}]}])
        with self.assertRaisesRegex(RuntimeError, '工具未执行'):
            await self.run_client(client)
        receipt = self.receipt()
        self.assertEqual(receipt['delivery_status'], 'FAILED')
        self.assertEqual(receipt['failure_category'], 'UNPARSED_TOOL_CALL')
        self.assertEqual(receipt['finish_reason'], 'stop')

    async def test_network_timeout_is_unknown_and_no_fake_zero_usage(self):
        client=await self.client(timeout=True)
        with self.assertRaisesRegex(RuntimeError,'超时'): await self.run_client(client)
        receipt=self.receipt()
        self.assertEqual(receipt['result_status'],'UNKNOWN')
        self.assertIsNone(receipt['response_id'])
        self.assertIsNone(receipt['usage'])
        self.assertEqual(receipt['failure_category'],'NETWORK_TIMEOUT')
        self.assertNotIn('not-safe-request-body',json.dumps(self.observed))

    async def test_early_eof_is_unknown_even_with_done_marker(self):
        client=await self.client([{'id':RESPONSE_ID,'choices':[{'delta':{'content':'partial'}}]}])
        with self.assertRaisesRegex(RuntimeError,'中断'): await self.run_client(client)
        receipt=self.receipt()
        self.assertEqual(receipt['result_status'],'UNKNOWN')
        self.assertEqual(receipt['response_id'],RESPONSE_ID)
        self.assertEqual(receipt['failure_category'],'EARLY_EOF')

    async def test_http_rejection_records_only_safe_code_and_trace(self):
        client=await self.client(status=400,error={'error':{'code':'Arrearage','message':'private-test-body dummy-secret'},'request_id':RID})
        with self.assertRaisesRegex(RuntimeError,'HTTP 400'): await self.run_client(client)
        receipt=self.receipt()
        self.assertEqual(receipt['result_status'],'FAILED')
        self.assertEqual(receipt['provider_error_code'],'Arrearage')
        self.assertEqual(receipt['rejection_category'],'BILLING')
        self.assertEqual(receipt['provider_request_id'],RID)

    async def test_stream_error_keeps_unknown_and_reported_trace(self):
        client=await self.client([{'id':RESPONSE_ID,'request_id':RID,'error':{'code':'DataInspectionFailed','message':'private-test-body'}}])
        with self.assertRaisesRegex(RuntimeError,'流返回错误'): await self.run_client(client)
        receipt=self.receipt()
        self.assertEqual(receipt['result_status'],'UNKNOWN')
        self.assertEqual(receipt['provider_error_code'],'DataInspectionFailed')
        self.assertEqual(receipt['provider_request_id'],RID)

    async def test_invalid_ids_and_usage_never_saved_as_free_text_or_zero(self):
        client=await self.client([{'id':'private-test-body','request_id':'dummy-secret','choices':[{'delta':{'content':'ok'},'finish_reason':'stop'}],
            'usage':{'prompt_tokens':-1,'completion_tokens':True,'total_tokens':'private-test-body'}}])
        await self.run_client(client)
        receipt=self.receipt()
        self.assertIsNone(receipt['response_id']);self.assertIsNone(receipt.get('provider_request_id'))
        self.assertEqual(receipt['response_id_status'],'UNRECOGNISED');self.assertEqual(receipt['provider_request_id_status'],'UNRECOGNISED')
        self.assertIsNone(receipt['usage']);self.assertEqual(receipt['usage_status'],'INVALID')

    async def test_conflicting_response_ids_and_finishes_not_silently_overwritten(self):
        client=await self.client([{'id':RESPONSE_ID,'choices':[{'delta':{'content':'ok'},'finish_reason':'stop'}]},
            {'id':'chatcmpl-abcdef1234567890','choices':[{'delta':{},'finish_reason':'tool_calls'}]}])
        await self.run_client(client)
        receipt=self.receipt()
        self.assertEqual(receipt['response_id_status'],'CONFLICT');self.assertIsNone(receipt['response_id'])
        self.assertEqual(receipt['finish_reason_status'],'CONFLICT');self.assertEqual(receipt['result_status'],'UNKNOWN')

    async def test_caller_close_preserves_unknown_without_resubmitting(self):
        client=await self.client([{'id':RESPONSE_ID,'choices':[{'delta':{'content':'partial'}}]}])
        with patch.dict('os.environ',{'IN1_FAKE_KEY':'dummy-secret'}):
            stream=client.chat_stream_with_tools([],request_observer=lambda event,data:self.observed.append((event,data)))
            await anext(stream);await stream.aclose()
        receipt=self.receipt()
        self.assertEqual(receipt['result_status'],'UNKNOWN');self.assertEqual(receipt['delivery_status'],'CANCELLED')

    async def test_tool_finish_and_missing_usage_are_distinct_from_failure(self):
        client=await self.client([{'id':RESPONSE_ID,'choices':[{'delta':{'tool_calls':[{'index':0,'id':'call','function':{'name':'read','arguments':'{}'}}]},'finish_reason':'tool_calls'}]}])
        await self.run_client(client)
        receipt=self.receipt()
        self.assertEqual(receipt['result_status'],'COMPLETE');self.assertEqual(receipt['finish_reason'],'tool_calls')
        self.assertIsNone(receipt['usage']);self.assertEqual(receipt['usage_status'],'NOT_REPORTED')

    async def broken_tail(self,finish=None):
        client=await self.client()
        await client._client.aclose()
        class Broken(httpx.AsyncByteStream):
            async def __aiter__(self):
                yield ('data: '+json.dumps({'id':RESPONSE_ID,'choices':[{'delta':{'content':'partial'},'finish_reason':finish}]})+'\n\n').encode()
                raise httpx.ReadTimeout('private-test-body')
        def handler(request):
            self.requests.append(json.loads(request.content))
            return httpx.Response(200,stream=Broken(),headers={'x-request-id':RID})
        client._client=httpx.AsyncClient(transport=httpx.MockTransport(handler))
        return client

    async def test_partial_stream_timeout_keeps_id_but_remains_unknown(self):
        client=await self.broken_tail()
        with self.assertRaisesRegex(RuntimeError,'超时'): await self.run_client(client)
        receipt=self.receipt()
        self.assertEqual(receipt['result_status'],'UNKNOWN')
        self.assertEqual(receipt['response_id'],RESPONSE_ID);self.assertEqual(receipt['provider_request_id'],RID)

    async def test_timeout_after_known_finish_does_not_erase_provider_completion(self):
        client=await self.broken_tail('stop')
        with self.assertRaisesRegex(RuntimeError,'超时'): await self.run_client(client)
        receipt=self.receipt()
        self.assertEqual(receipt['result_status'],'COMPLETE');self.assertEqual(receipt['delivery_status'],'FAILED')
        self.assertEqual(receipt['finish_reason'],'stop');self.assertIsNone(receipt['usage'])

    async def test_malformed_stream_records_unknown_without_echoing_raw_packet(self):
        client=await self.client(raw='data: private-test-body\n\n')
        with self.assertRaises(ValueError): await self.run_client(client)
        receipt=self.receipt()
        self.assertEqual(receipt['result_status'],'UNKNOWN');self.assertEqual(receipt['failure_category'],'STREAM_PROTOCOL_ERROR')

    async def test_conflicting_request_ids_and_partial_usage_not_invented(self):
        client=await self.client([{'request_id':'abcdef12-1234-1234-1234-123456789abc',
            'choices':[{'delta':{'content':'answer'},'finish_reason':'stop'}],'usage':{'prompt_tokens':20}}],headers={'x-request-id':RID})
        await self.run_client(client)
        receipt=self.receipt()
        self.assertEqual(receipt['provider_request_id_status'],'CONFLICT')
        self.assertNotIn('provider_request_id',receipt)
        self.assertEqual(receipt['usage'],{'prompt_tokens':20});self.assertNotIn('total_tokens',receipt['usage'])

    async def test_agent_passes_terminal_metadata_with_session_context(self):
        from types import SimpleNamespace
        from tests.test_stock_source_quotes import SourceQuoteLoopTests
        client=await self.client([{'id':RESPONSE_ID,'choices':[{'delta':{'content':'已核对。'},'finish_reason':'stop'}]}])
        client.cloud=False
        agent=SourceQuoteLoopTests.agent(self,client,SimpleNamespace(),[])
        agent.audit.record=lambda event,data:self.observed.append((event,data))
        with patch.dict('os.environ',{'IN1_FAKE_KEY':'dummy-secret'}):
            events=[e async for e in agent.run([{'role':'user','content':'只解释给定内容。'}],session_id='receipt-test')]
        receipt=self.receipt()
        self.assertEqual(events[-1].kind,'done')
        self.assertEqual(receipt['session_id'],'receipt-test');self.assertIsNone(receipt['view_version'])

    async def test_http_503_remains_unknown_even_when_error_body_available(self):
        client=await self.client(status=503,error={'error':{'code':'InternalError','message':'private-test-body'}},headers={'x-request-id':RID})
        with self.assertRaisesRegex(RuntimeError,'HTTP 503'): await self.run_client(client)
        receipt=self.receipt()
        self.assertEqual(receipt['result_status'],'UNKNOWN');self.assertEqual(receipt['http_status'],503)
        self.assertEqual(receipt['provider_request_id'],RID)

    async def test_conflicting_ids_alone_cannot_claim_complete_provider_result(self):
        client=await self.client([{'id':RESPONSE_ID,'choices':[{'delta':{'content':'a'}}]},
            {'id':'chatcmpl-abcdef1234567890','choices':[{'delta':{'content':'b'},'finish_reason':'stop'}]}])
        await self.run_client(client)
        receipt=self.receipt()
        self.assertEqual(receipt['response_id_status'],'CONFLICT');self.assertEqual(receipt['result_status'],'UNKNOWN')
