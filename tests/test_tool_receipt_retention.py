"""Known tool replies survive a subsequent plugin/checkpoint failure."""
import json
import unittest
from unittest.mock import patch

import httpx

from core.agent import Agent
from bff.schemas import ChatRequest
from tests import test_in1


class ReceiptRetentionTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        await test_in1.ServiceTests.asyncSetUp(self)
        self.model_calls = 0
        self.tool_calls = 0

    async def run_failure(self, result, *, before_receipt=False):
        parent = self
        class Model:
            cloud = True
            model = 'qwen3.5-flash'
            async def chat_stream_with_tools(self, messages, tools=None, **kwargs):
                parent.model_calls += 1
                yield '', {'role':'assistant', 'content':'', 'tool_calls':[{
                    'id':'original-tool', 'type':'function', 'function':{
                        'name':'file_read', 'arguments':'{"path":"/workspace/source.txt"}'}}]}
            async def close(self):
                pass
        self.runtime.models.clients['qwen:qwen3.5-flash'] = Model()
        async def dispatch(*args, **kwargs):
            self.tool_calls += 1
            if isinstance(result, Exception):
                raise result
            yield {'event':'result', 'result':result}
        self.runtime.router.dispatch_stream = dispatch
        execute = Agent._execute_tools
        observed = []
        async def projection_failure(agent, *args, **kwargs):
            async for event in execute(agent, *args, **kwargs):
                if event.kind == 'message':
                    observed.append(event.data['message'].get('content', ''))
                if (event.kind == 'tool_end' or
                        (before_receipt and event.kind == 'message')):
                    raise httpx.ConnectError('checkpoint connection failed')
                yield event
        with patch.object(Agent, '_execute_tools', projection_failure):
            events = [e async for e in self.service.stream_chat(ChatRequest(
                message='执行一次计算', model='qwen:qwen3.5-flash'))]
        run = self.service.runs.store.get(events[0].run_id)
        self.assertEqual(run['status'], 'FAILED')
        self.assertFalse(run['checkpoint']['safe'], {
            'blocker':run['checkpoint'].get('blocker'),
            'calls':(self.model_calls, self.tool_calls),
            'events':[e.event for e in events], 'error':run['error'], 'observed':observed})
        self.assertEqual((self.model_calls, self.tool_calls), (1, 1))
        self.assertIn('checkpoint connection failed', run['error'])
        rows = self.runtime.store.get_messages(events[0].conversation_id)
        replies = [json.loads(row.metadata).get('message', {}) for row in rows if row.role == 'protocol']
        replies = [m for m in replies if m.get('role') == 'tool' and m.get('tool_call_id') == 'original-tool']
        self.assertEqual(len(replies), 1)
        tools = [r for r in rows if r.role == 'tool']
        self.assertEqual(len(tools), 1)
        return tools[0], replies[0]

    async def test_known_parameter_error_is_not_replaced_by_interruption(self):
        error = 'file_read.path须为已授权的文件路径'
        row, reply = await self.run_failure(PermissionError(error))
        self.assertEqual(json.loads(row.tool_result), {'error':error})
        self.assertEqual(json.loads(reply['content']), {'error':error})
        self.assertEqual(json.loads(row.metadata)['status'], 'error')
        self.assertTrue(json.loads(row.metadata)['receipt_retained'])

    async def test_known_success_survives_without_claiming_run_completion(self):
        result = {'content':'actual original evidence', 'sha256':'a'*64}
        row, reply = await self.run_failure(result)
        self.assertEqual(json.loads(row.tool_result), result)
        self.assertEqual(json.loads(reply['content']), result)
        self.assertEqual(json.loads(row.metadata)['status'], 'ok')
        self.assertTrue(json.loads(row.metadata)['postprocessing_incomplete'])

    async def test_missing_delivered_receipt_keeps_unknown_interruption(self):
        row, reply = await self.run_failure({'content':'actual original evidence'}, before_receipt=True)
        self.assertIn('执行已中断', json.loads(row.tool_result)['error'])
        self.assertNotIn('content', json.loads(row.tool_result))
        self.assertNotIn('receipt_retained', json.loads(row.metadata))
        self.assertIn('执行已中断', json.loads(reply['content'])['error'])
