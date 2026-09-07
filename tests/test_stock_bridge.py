"""Offline tests for the stock read-only bridge; a fake in-contract broker stands in."""
import json
import unittest
from unittest.mock import patch

import httpx

from core import config
from core.stock_bridge import StockBridge
from core.tool_registry import ToolRegistry

REF = 'a' * 64
TICKET = 'b' * 32


def make_broker(calls, *, step_responses=None, finish_reply='', status=200):
    state = {'steps': 0}

    def handler(request):
        payload = json.loads(request.content)
        calls.append({'path': request.url.path, 'json': payload, 'auth': request.headers.get('authorization')})
        path = request.url.path
        if status != 200:
            return httpx.Response(status, json={'error': '需要独立总控桥接凭据'})
        if path.endswith('/prepare'):
            return httpx.Response(200, json={'ticket': TICKET, 'cloud_context': {}, 'tools': [], 'limits': {}})
        if path.endswith('/step'):
            if step_responses is None:
                state['steps'] += 1
                return httpx.Response(200, json=ok_step({}, seq=state['steps']))
            response = step_responses[state['steps']]
            state['steps'] += 1
            return httpx.Response(200, json=response)
        if path.endswith('/finish'):
            return httpx.Response(200, json={'route': 'direct', 'reply': finish_reply, 'cloud_observation': {}})
        return httpx.Response(404, json={'error': 'not found'})
    return handler


def ok_step(observation, *, local=False, seq=1):
    return {'sequence': seq, 'continue': True, 'status': 'TOOL_RETURNED',
            'observation': observation, 'tool': {}, 'code': 'OK', 'context': {},
            'local_result_available': local}


class BridgeTests(unittest.IsolatedAsyncioTestCase):
    def bridge(self, handler):
        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        self.addAsyncCleanup(client.aclose)
        return StockBridge('http://paper.test', 'secret-token', client=client)

    async def test_lifecycle_identity_and_attachment_harvest(self):
        calls = []
        reply = '本轮工具处理已结束，已取得的结果见下方。\n\n---\n\n#### 本轮本地结果\n\n报告全文一\n\n---\n\n报告全文二'
        bridge = self.bridge(make_broker(calls,
            step_responses=[ok_step({'kind': 'task_search', 'items': [{'reference': REF}]}),
                            ok_step({'task_status': 'SUCCEEDED'}, local=True, seq=2)],
            finish_reply=reply))
        bridge.set_turn('conv-1', 'req-1', '看看巨人网络的研究')
        found = await bridge.call_tool('stock_research_find', {'symbol': '002558'}, 'conv-1')
        self.assertEqual(found['model_observation']['items'][0]['reference'], REF)
        read = await bridge.call_tool('stock_report_read', {'reference': REF}, 'conv-1')
        self.assertTrue(read['local_result_available'])
        texts = await bridge.finish_turn('conv-1')
        self.assertEqual(texts, ['报告全文一', '报告全文二'])
        self.assertIsNone(await bridge.close())

        prepare = calls[0]
        self.assertTrue(prepare['path'].endswith('/prepare'))
        self.assertEqual(prepare['json']['actor'], 'local-ai-agent')
        self.assertEqual(prepare['json']['conversation'], 'conv-1')
        self.assertEqual(prepare['json']['request_id'], 'req-1')
        self.assertEqual(prepare['json']['query'], '看看巨人网络的研究')
        self.assertEqual(prepare['auth'], 'Bearer secret-token')
        steps = [c for c in calls if c['path'].endswith('/step')]
        self.assertEqual([s['json']['sequence'] for s in steps], [1, 2])
        self.assertEqual(steps[0]['json']['tool_call'], {'action': 'find', 'kind': 'research', 'symbol': '002558'})
        self.assertEqual(steps[1]['json']['tool_call'],
                         {'action': 'view', 'kind': 'job', 'reference': {'type': 'task', 'token': REF}})
        self.assertTrue(all(s['json']['ticket'] == TICKET for s in steps))
        finish = calls[-1]
        self.assertTrue(finish['path'].endswith('/finish'))
        self.assertEqual(finish['json']['completion'], '')

    async def test_error_mappings(self):
        calls = []
        bridge = self.bridge(make_broker(calls, step_responses=[
            {'sequence': 1, 'continue': True, 'status': 'RULE_BLOCKED', 'observation': {}, 'code': 'REFERENCE_NOT_OWNED'},
        ]))
        bridge.set_turn('c', 'r', 'q')
        result = await bridge.call_tool('stock_account_view', {}, 'c')
        self.assertIn('REFERENCE_NOT_OWNED', result['error'])
        self.assertEqual(result['status'], 'RULE_BLOCKED')

        calls.clear()
        bridge = self.bridge(make_broker(calls, step_responses=[
            {'sequence': 1, 'continue': False, 'status': 'STEP_LIMIT', 'observation': {}},
        ]))
        bridge.set_turn('c', 'r', 'q')
        result = await bridge.call_tool('stock_account_view', {}, 'c')
        self.assertIn('上限', result['error'])

        bridge = self.bridge(make_broker([], status=401))
        bridge.set_turn('c', 'r', 'q')
        result = await bridge.call_tool('stock_account_view', {}, 'c')
        self.assertIn('认证失败', result['error'])

    async def test_unreachable_backend_is_clear_error(self):
        def handler(request):
            raise httpx.ConnectError('refused')
        bridge = self.bridge(handler)
        bridge.set_turn('c', 'r', 'q')
        result = await bridge.call_tool('stock_account_view', {}, 'c')
        self.assertIn('无法连接', result['error'])

    async def test_reference_validated_before_any_http_call(self):
        calls = []
        bridge = self.bridge(make_broker(calls))
        bridge.set_turn('c', 'r', 'q')
        result = await bridge.call_tool('stock_report_read', {'reference': 'not-a-token'}, 'c')
        self.assertIn('stock_research_find', result['error'])
        self.assertEqual(calls, [])

    async def test_finish_without_session_or_attachments(self):
        calls = []
        bridge = self.bridge(make_broker(calls, finish_reply='没有附件。'))
        self.assertEqual(await bridge.finish_turn('nobody'), [])
        bridge.set_turn('c', 'r', 'q')
        await bridge.call_tool('stock_research_find', {}, 'c')
        self.assertEqual(await bridge.finish_turn('c'), [])


class RegistryGatingTests(unittest.TestCase):
    def test_stock_tools_gated_on_configuration(self):
        without = ToolRegistry(config.TOOLS_DIR)
        self.assertNotIn('stock_account_view', without.known_tools)
        with_url = ToolRegistry(config.TOOLS_DIR, stock_bridge_url='http://127.0.0.1:8766')
        self.assertIn('stock_account_view', with_url.known_tools)
        self.assertIn('stock_research_find', with_url.known_tools)
        self.assertIn('stock_report_read', with_url.known_tools)


class RouterDelegationTests(unittest.IsolatedAsyncioTestCase):
    async def test_router_delegates_stock_backend(self):
        import tempfile
        from pathlib import Path
        from types import SimpleNamespace
        from core.audit_logger import AuditLogger
        from core.policy_engine import PolicyEngine
        from core.tool_router import ToolRouter
        with tempfile.TemporaryDirectory() as tmp:
            registry = ToolRegistry(config.TOOLS_DIR, stock_bridge_url='http://x')
            bridge = SimpleNamespace(calls=[])
            async def call_tool(tool, params, session_id):
                bridge.calls.append((tool, params, session_id))
                return {'model_observation': {'ok': True}}
            bridge.call_tool = call_tool
            router = ToolRouter(config.SKILL_FILES_URL, config.SKILL_RUNNER_URL, config.SKILL_WEBSEARCH_URL,
                                PolicyEngine(config.POLICY_PATH), AuditLogger(Path(tmp)/'audit.jsonl'),
                                registry, stock_bridge=bridge)
            self.addAsyncCleanup(router.close)
            result = await router.dispatch('stock_account_view', {}, 'conv-9')
            self.assertEqual(result['model_observation'], {'ok': True})
            self.assertEqual(bridge.calls, [('stock_account_view', {}, 'conv-9')])
            without = ToolRouter(config.SKILL_FILES_URL, config.SKILL_RUNNER_URL, config.SKILL_WEBSEARCH_URL,
                                 PolicyEngine(config.POLICY_PATH), AuditLogger(Path(tmp)/'audit2.jsonl'), registry)
            self.addAsyncCleanup(without.close)
            with self.assertRaisesRegex(RuntimeError, '未配置'):
                await without.dispatch('stock_account_view', {}, 'conv-9')


if __name__ == '__main__':
    unittest.main()
