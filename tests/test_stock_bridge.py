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

    async def test_empty_observation_surfaces_code_to_model(self):
        calls = []
        bridge = self.bridge(make_broker(calls, step_responses=[
            {'sequence': 1, 'continue': True, 'status': 'TOOL_RETURNED', 'observation': {}, 'code': 'UNINITIALIZED', 'local_result_available': True},
        ]))
        bridge.set_turn('c', 'r', 'q')
        result = await bridge.call_tool('stock_account_view', {}, 'c')
        self.assertEqual(result['model_observation']['code'], 'UNINITIALIZED')
        self.assertIn('note', result['model_observation'])

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


class ResearchChainActionTests(unittest.IsolatedAsyncioTestCase):
    def bridge(self, handler):
        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        self.addAsyncCleanup(client.aclose)
        return StockBridge('http://paper.test', 'secret-token', client=client)

    async def test_submit_mapping_and_capsule_receipt(self):
        calls = []
        capsule = {'action': 'submit', 'kind': 'research', 'status': 'QUEUED', 'operation': 'CREATED', 'symbol': '600150'}
        bridge = self.bridge(make_broker(calls, step_responses=[
            {'sequence': 1, 'continue': True, 'status': 'TOOL_RETURNED', 'observation': {}, 'tool': capsule, 'code': 'TASK_SUBMITTED', 'local_result_available': True},
        ]))
        bridge.set_turn('c', 'r', '研究 600150')
        result = await bridge.call_tool('stock_research_submit', {'symbol': '600150'}, 'c')
        self.assertEqual(result['model_observation']['status'], 'QUEUED')
        self.assertEqual(result['model_observation']['operation'], 'CREATED')
        step = [c for c in calls if c['path'].endswith('/step')][0]
        self.assertEqual(step['json']['tool_call'], {'action': 'submit', 'kind': 'research', 'symbol': '600150'})

    async def test_submit_by_stock_name_maps_to_name_reference(self):
        calls = []
        capsule = {'action': 'submit', 'kind': 'research', 'status': 'QUEUED', 'operation': 'CREATED', 'symbol': '600150'}
        bridge = self.bridge(make_broker(calls, step_responses=[
            {'sequence': 1, 'continue': True, 'status': 'TOOL_RETURNED', 'observation': {}, 'tool': capsule, 'code': 'TASK_SUBMITTED', 'local_result_available': True},
        ]))
        bridge.set_turn('c', 'r', '研究一下中国船舶')
        result = await bridge.call_tool('stock_research_submit', {'name': ' 中国船舶 '}, 'c')
        self.assertEqual(result['model_observation']['symbol'], '600150')
        step = [c for c in calls if c['path'].endswith('/step')][0]
        self.assertEqual(step['json']['tool_call'], {'action': 'submit', 'kind': 'research',
                         'reference': {'type': 'candidate_name', 'name': '中国船舶'}})

    async def test_submit_name_with_research_mode(self):
        calls = []
        bridge = self.bridge(make_broker(calls))
        bridge.set_turn('c', 'r', 'q')
        await bridge.call_tool('stock_research_submit', {'name': '贵州茅台', 'research_mode': 'redo'}, 'c')
        step = [c for c in calls if c['path'].endswith('/step')][0]
        self.assertEqual(step['json']['tool_call'], {'action': 'submit', 'kind': 'research', 'research_mode': 'redo',
                         'reference': {'type': 'candidate_name', 'name': '贵州茅台'}})

    async def test_submit_rejects_symbol_plus_name(self):
        calls = []
        bridge = self.bridge(make_broker(calls))
        bridge.set_turn('c', 'r', 'q')
        result = await bridge.call_tool('stock_research_submit', {'symbol': '600150', 'name': '中国船舶'}, 'c')
        self.assertIn('二选一', result['error'])
        self.assertEqual(calls, [])

    async def test_submit_requires_symbol_or_name(self):
        calls = []
        bridge = self.bridge(make_broker(calls))
        bridge.set_turn('c', 'r', 'q')
        result = await bridge.call_tool('stock_research_submit', {'research_mode': 'reuse'}, 'c')
        self.assertIn('symbol', result['error'])
        self.assertIn('name', result['error'])
        self.assertEqual(calls, [])

    async def test_boundary_is_terminal_for_the_turn(self):
        calls = []
        bridge = self.bridge(make_broker(calls, step_responses=[
            ok_step({'kind': 'task_search', 'items': []}),
            {'sequence': 2, 'continue': False, 'status': 'STEP_LIMIT', 'observation': {}},
        ]))
        bridge.set_turn('c', 'r', 'q')
        await bridge.call_tool('stock_research_find', {}, 'c')
        limited = await bridge.call_tool('stock_research_find', {}, 'c')
        self.assertEqual(limited['status'], 'STEP_LIMIT')
        # Boundary is terminal: further calls are answered locally without HTTP.
        again = await bridge.call_tool('stock_account_view', {}, 'c')
        self.assertEqual(again['status'], 'STEP_LIMIT')
        steps = [c for c in calls if c['path'].endswith('/step')]
        self.assertEqual(len(steps), 2)

    async def test_submit_validation_happens_before_http(self):
        calls = []
        bridge = self.bridge(make_broker(calls))
        bridge.set_turn('c', 'r', 'q')
        self.assertIn('6 位', (await bridge.call_tool('stock_research_submit', {'symbol': 'ABC'}, 'c'))['error'])
        self.assertIn('research_mode', (await bridge.call_tool('stock_research_submit', {'symbol': '600150', 'research_mode': 'again'}, 'c'))['error'])
        self.assertEqual(calls, [])

    async def test_cancel_and_resume_mapping(self):
        calls = []
        bridge = self.bridge(make_broker(calls))
        bridge.set_turn('c', 'r', 'q')
        await bridge.call_tool('stock_research_cancel', {'reference': REF}, 'c')
        await bridge.call_tool('stock_research_resume', {'reference': REF}, 'c')
        steps = [c['json']['tool_call'] for c in calls if c['path'].endswith('/step')]
        self.assertEqual(steps, [
            {'action': 'cancel_research', 'reference': {'type': 'task', 'token': REF}},
            {'action': 'resume_research', 'reference': {'type': 'task', 'token': REF}},
        ])

    async def test_cancel_resume_reference_validated(self):
        calls = []
        bridge = self.bridge(make_broker(calls))
        bridge.set_turn('c', 'r', 'q')
        result = await bridge.call_tool('stock_research_cancel', {'reference': 'bad'}, 'c')
        self.assertIn('stock_research_find', result['error'])
        self.assertEqual(calls, [])


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

from tests import test_in1
from bff.schemas import ChatRequest

class StockTurnTests(unittest.IsolatedAsyncioTestCase):
    """Service-level turn wiring with a stub bridge; no broker or model calls."""
    asyncSetUp = test_in1.ServiceTests.asyncSetUp

    def install_bridge(self, texts):
        from core.tool_registry import ToolRegistry
        parent = self
        class StubBridge:
            def __init__(self):
                self.turns = []
                self.open = False
            def set_turn(self, cid, rid, query=''):
                self.turns.append(('set', cid, rid, query))
                self.open = True
            def has_open_turn(self, cid):
                return self.open
            async def call_tool(self, tool, params, session_id):
                self.turns.append(('call', tool, session_id))
                return {'model_observation': {'task_status': 'SUCCEEDED'}, 'status': 'TOOL_RETURNED',
                        'code': 'OK', 'local_result_available': True}
            async def finish_turn(self, cid):
                if not self.open:
                    return []
                self.turns.append(('finish', cid))
                self.open = False
                return list(texts)
            async def close(self): pass
        stub = StubBridge()
        registry = ToolRegistry(config.TOOLS_DIR, stock_bridge_url='http://x')
        self.runtime.stock_bridge = stub
        self.runtime.tool_registry = registry
        self.runtime.router._stock_bridge = stub
        self.runtime.router._registry = registry
        return stub

    def stock_model(self):
        parent = self
        ref = 'a' * 64
        class StockModel:
            cloud = True
            model = 'qwen3.5-flash'
            async def chat_stream_with_tools(self, messages, tools=None):
                if not any(m.get('role') == 'tool' for m in messages):
                    yield '', {'role':'assistant','content':'','tool_calls':[{'id':'c1','type':'function','function':{'name':'stock_report_read','arguments':json.dumps({'reference': ref})}}]}
                else:
                    yield '报告已读取', None
                    yield '', {'role':'assistant','content':'报告已读取'}
            async def close(self): pass
        self.runtime.models.clients['qwen:qwen3.5-flash'] = StockModel()
        return ref

    async def test_local_result_attached_at_turn_end_not_in_protocol(self):
        stub = self.install_bridge(['完整报告全文'])
        self.stock_model()
        events = [e async for e in self.service.stream_chat(ChatRequest(message='读报告', model='qwen3.5-flash', request_id='stock-turn-1'))]
        cid = events[0].conversation_id
        self.assertIn(('set', cid, 'stock-turn-1', '读报告'), stub.turns)
        self.assertIn(('finish', cid), stub.turns)
        tool_rows = [m for m in self.service.get_messages(cid) if m.role == 'tool']
        self.assertEqual(tool_rows[0].tool_result['local_result'], '完整报告全文')
        local_events = [e for e in events if e.event == 'tool.local_result']
        self.assertEqual(len(local_events), 1)
        self.assertEqual(local_events[0].block_id, 'c1')
        protocol = [m for m in self.runtime.store.get_messages(cid) if m.role == 'protocol']
        self.assertNotIn('完整报告全文', json.dumps([m.metadata for m in protocol], ensure_ascii=False))

    async def test_answer_only_regenerate_opens_no_broker_session(self):
        stub = self.install_bridge(['X'])
        self.stock_model()
        events = [e async for e in self.service.stream_chat(ChatRequest(message='读报告', model='qwen:qwen3.5-flash', request_id='stock-turn-2'))]
        cid = events[0].conversation_id
        before = len(stub.turns)
        _ = [e async for e in self.service.regenerate_chat(cid, request_id='stock-turn-3')]
        self.assertEqual(len(stub.turns), before)

    async def test_interrupt_finishes_bridge_session_without_events(self):
        stub = self.install_bridge(['X'])
        parent = self
        ref = 'a' * 64
        class HangingModel:
            cloud = True
            model = 'qwen3.5-flash'
            async def chat_stream_with_tools(self, messages, tools=None):
                if not any(m.get('role')=='tool' for m in messages):
                    yield '', {'role':'assistant','content':'','tool_calls':[{'id':'c1','type':'function','function':{'name':'stock_report_read','arguments':json.dumps({'reference': ref})}}]}
                    return
                yield '部分', None
                import asyncio
                await asyncio.Event().wait()
                yield '', {'role':'assistant','content':'never'}
            async def close(self): pass
        self.runtime.models.clients['qwen:qwen3.5-flash'] = HangingModel()
        collected = []
        stream = self.service.stream_chat(ChatRequest(message='go', model='qwen3.5-flash', request_id='stock-int-1'))
        async for event in stream:
            collected.append(event)
            if event.event == 'assistant.delta':
                break
        await stream.aclose()
        self.assertFalse(any(e.event == 'tool.local_result' for e in collected))
        self.assertTrue(any(t[0] == 'finish' for t in stub.turns))
        # Attachment harvested to storage silently, not streamed.
        cid = next(t[1] for t in stub.turns if t[0] == 'set')
        tool_rows = [m for m in self.service.get_messages(cid) if m.role == 'tool']
        self.assertEqual(tool_rows[0].tool_result.get('local_result'), 'X')

class StockPrivacyTests(unittest.IsolatedAsyncioTestCase):
    """Local stock report content must never reach the model through any path."""
    asyncSetUp = test_in1.ServiceTests.asyncSetUp

    def install_bridge(self, marker):
        from core.tool_registry import ToolRegistry
        parent = self
        class StubBridge:
            def __init__(self): self.open = False
            def set_turn(self, cid, rid, query=''): self.open = True
            def has_open_turn(self, cid): return self.open
            async def call_tool(self, tool, params, session_id):
                return {'model_observation': {'task_status': 'SUCCEEDED'}, 'status': 'TOOL_RETURNED',
                        'code': 'OK', 'local_result_available': True}
            async def finish_turn(self, cid):
                self.open = False
                return [marker]
            async def close(self): pass
        stub = StubBridge()
        self.runtime.stock_bridge = stub
        self.runtime.router._stock_bridge = stub
        registry = ToolRegistry(config.TOOLS_DIR, stock_bridge_url='http://x')
        self.runtime.tool_registry = registry
        self.runtime.router._registry = registry
        return stub

    def recording_model(self):
        parent = self
        ref = 'a' * 64
        class RecordingModel:
            cloud = True
            model = 'qwen3.5-flash'
            async def chat_stream_with_tools(self, messages, tools=None):
                parent.seen.append(json.loads(json.dumps(messages)))
                if not any(m.get('role') == 'tool' for m in messages):
                    yield '', {'role':'assistant','content':'','tool_calls':[{'id':'c1','type':'function','function':{'name':'stock_report_read','arguments':json.dumps({'reference': ref})}}]}
                else:
                    yield '好的', None
                    yield '', {'role':'assistant','content':'好的'}
            async def close(self): pass
        self.seen = []
        self.runtime.models.clients['qwen:qwen3.5-flash'] = RecordingModel()

    async def test_local_result_never_reenters_model_history(self):
        marker = 'F01-SECRET-REPORT-TEXT'
        self.install_bridge(marker)
        self.recording_model()
        _ = [e async for e in self.service.stream_chat(ChatRequest(message='读报告', model='qwen3.5-flash', request_id='stock-leak-1'))]
        cid = _[0].conversation_id
        _ = [e async for e in self.service.stream_chat(ChatRequest(conversation_id=cid, message='再聊聊', model='qwen3.5-flash', request_id='stock-leak-2'))]
        self.assertGreaterEqual(len(self.seen), 2)
        self.assertNotIn(marker, json.dumps(self.seen, ensure_ascii=False))

    async def test_history_projection_and_compact_exclude_local_result(self):
        marker = 'F01-SECRET-REPORT-TEXT'
        conv = self.runtime.store.create_conversation(title='t', model='qwen:qwen3.5-flash')
        user = self.runtime.store.add_message(conv.id, role='user', content='读报告', metadata={'cloud_safe': True})
        self.runtime.store.add_message(conv.id, role='protocol', content='', response_to_message_id=user.id,
            metadata={'cloud_safe': True, 'message': {'role':'tool','tool_call_id':'c1','tool_name':'stock_report_read','content':'{"task_status":"SUCCEEDED"}'}})
        self.runtime.store.add_message(conv.id, role='tool', content='[ok] stock_report_read 已完成', response_to_message_id=user.id,
            tool_name='stock_report_read', tool_result={'model_observation': {'task_status':'SUCCEEDED'}, 'local_result': marker}, metadata={'cloud_safe': True})
        for cloud in (False, True):
            history = self.runtime.store.messages_as_dicts(conv.id, cloud=cloud)
            self.assertNotIn(marker, json.dumps(history, ensure_ascii=False))
        from core.context_manager import ContextManager
        captured = []
        class Model:
            async def chat(self, *args):
                captured.append(json.dumps(args, ensure_ascii=False))
                return '摘要'
        manager = ContextManager(context_window=100, compact_threshold=.1, llm=Model())
        history = self.runtime.store.messages_as_dicts(conv.id)
        compacted = await manager.auto_compact(history)
        self.assertNotIn(marker, json.dumps(compacted, ensure_ascii=False))
        self.assertFalse(any(marker in text for text in captured))

    async def test_cross_session_tools_exclude_local_result(self):
        marker = 'F01-SECRET-REPORT-TEXT'
        conv = self.runtime.store.create_conversation(title='t', model='qwen:qwen3.5-flash')
        user = self.runtime.store.add_message(conv.id, role='user', content='看报告', metadata={'cloud_safe': True})
        self.runtime.store.add_message(conv.id, role='tool', content='[ok] stock_report_read 已完成', response_to_message_id=user.id,
            tool_name='stock_report_read', tool_result={'local_result': marker}, metadata={'cloud_safe': True})
        router = self.runtime.router
        router.cloud = True
        found = router._dispatch_local('conversation_search', {'query': '报告'})
        self.assertNotIn(marker, json.dumps(found, ensure_ascii=False))
        read = router._dispatch_local('conversation_read', {'conversation_id': conv.id})
        self.assertNotIn(marker, json.dumps(read, ensure_ascii=False))
