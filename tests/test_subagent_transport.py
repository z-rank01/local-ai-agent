import json
import unittest
from pathlib import Path
from types import SimpleNamespace
from core.agent import Agent
from core.context_manager import ContextManager
from core.subagent_executor import AgentSubagentExecutor
from core.subagent_store import RunSubagentStore
from harness.contracts.subagents import RoleTask
from harness.contracts.tool_provider import ExecutionContext
from harness.runtime.subagents.transport import SubagentTransport
from tests.subagent_fixtures import MemoryStore, role, complete_result
from plugins.finance.subagents import FinanceSubagentExtension


class SubagentTransportTests(unittest.IsolatedAsyncioTestCase):
    async def test_bind_create_result_and_stop(self):
        class Executor:
            async def run(self, task, context):
                return complete_result(task.role.role_id)
        transport = SubagentTransport({'evidence':role('evidence')}).bind(
            ExecutionContext('default', Path('.'), Path, subagent_executor=Executor(),
                             subagent_store=MemoryStore()))
        created = await transport.call_tool('agent_batch_create', {
            'tasks':[{'role_id':'evidence','task':'读取'}]}, 'c')
        result = await transport.call_tool('agent_batch_result', {'batch_id':created['batch_id']}, 'c')
        self.assertTrue(result['complete'])
        self.assertEqual(result['results']['evidence']['artifacts'][0]['content'], 'ok')
        stopped = await transport.call_tool('agent_batch_stop', {'batch_id':created['batch_id']}, 'c')
        self.assertTrue(stopped['complete'])
        missing = SubagentTransport({'evidence':role('evidence')}).bind(
            ExecutionContext('default', Path('.'), Path))
        with self.assertRaisesRegex(RuntimeError, '当前聊天运行上下文'):
            await missing.call_tool('agent_batch_create', {
                'tasks':[{'role_id':'evidence','task':'读取'}]}, 'c')

    async def test_result_view_does_not_duplicate_full_tool_receipts(self):
        from harness.runtime.subagents.transport import _view
        batch = {'batch_id':'b', 'results':{'evidence':{'status':'COMPLETE', 'result_ref':'actual-ref',
            'raw_text':'full-role-output', 'artifacts':[{'kind':'analysis','content':'kept'}],
            'native_receipt':{'tools':[{'name':'web_fetch','call_id':'actual-call',
                                      'status':'ok','result':{'content':'full-source'}}]}}}}
        view = _view(batch)
        self.assertEqual(view['results']['evidence']['result_ref'], 'actual-ref')
        self.assertNotIn('raw_text', view['results']['evidence'])
        self.assertNotIn('full-source', json.dumps(view))
        self.assertEqual(view['results']['evidence']['native_receipt']['tools'][0]['call_id'], 'actual-call')
        self.assertEqual(batch['results']['evidence']['raw_text'], 'full-role-output')

    async def test_run_store_recovers_results_from_parent_checkpoint(self):
        saved = {}
        checkpoint = {}
        class Store:
            def get(self, run_id):
                return {'checkpoint':checkpoint}
            def update(self, run_id, **fields):
                checkpoint.update(fields)
        manager = SimpleNamespace(store=Store(), publish=lambda *args:None)
        control = SimpleNamespace(id='parent', manager=manager)
        class Context:
            def __init__(self):
                self.evidence_ids = set()
                self.serial = 0
            def archive(self, value):
                self.serial += 1
                key = 'evidence-' + str(self.serial)
                saved[key] = value.get('content') or ''
                return key
            def read_evidence(self, key, cursor=0):
                content = saved[key]
                end = min(len(content), cursor + 37)
                return {'content':content[cursor:end], 'next_cursor':end if end < len(content) else None}
        store = RunSubagentStore(control, Context())
        task = RoleTask(role=role('evidence'), text='读取', batch_id='batch')
        store.create_batch('batch', (task,))
        store.save_result('batch', complete_result('evidence', {'text':'x' * 100}))
        batch = store.get_batch('batch')
        self.assertEqual(batch['results']['evidence']['status'], 'COMPLETE')
        self.assertEqual(batch['results']['evidence']['artifacts'][0]['content'], {'text':'x' * 100})
        self.assertIn('result_ref', batch['results']['evidence'])
        self.assertIn('subagent_batches', checkpoint)
        self.assertTrue(all(saved.values()))
        with self.assertRaisesRegex(ValueError, '不存在'):
            store.get_batch('missing')
        with self.assertRaisesRegex(ValueError, '不存在'):
            store.save_result('missing', complete_result('evidence'))

    async def test_tool_free_executor_uses_explicit_evidence(self):
        class Gateway:
            async def generate(self, request):
                return {'status':'COMPLETE','text':
                    '{"artifacts":[{"kind":"analysis","content":{"answer":"ok"}}]}', 'usage':{'total_tokens':1}}
        context = SimpleNamespace(read_evidence=lambda key:{'content':'evidence-' + key})
        agent = SimpleNamespace(registry=SimpleNamespace(known_tools={'file_read'}), context_mgr=context)
        executor = AgentSubagentExecutor(agent)
        result = await executor.run(RoleTask(role=role('analysis'), text='分析',
            evidence_ids=('e1',), batch_id='batch'), SimpleNamespace(model_gateway=Gateway()))
        self.assertEqual(result.artifacts[0].content, {'answer':'ok'})
        from core.subagent_store import preserve_subagent_batches
        merged = preserve_subagent_batches({'subagent_batches':{'batch':{'role_ids':['evidence']}}}, {})
        self.assertIn('subagent_batches', merged)
        class InvalidGateway:
            async def generate(self, request):
                return {'status':'COMPLETE','text':
                    '{"artifacts":[{"kind":"other","content":{}}],"issues":[]}'}
        invalid = await executor.run(RoleTask(role=role('analysis'), text='分析',
            batch_id='batch'), SimpleNamespace(model_gateway=InvalidGateway()))
        self.assertEqual(invalid.status, 'FAILED')
        self.assertIn('other', invalid.raw_text)

    async def test_dependency_receives_actual_prior_artifacts_not_author_prose(self):
        from tests.subagent_fixtures import MemoryStore
        store = MemoryStore()
        batch_id = 'batch'
        store.create_batch(batch_id, (RoleTask(role=role('evidence'), text='取证'),))
        store.save_result(batch_id, complete_result('evidence', {'source':'actual'}))
        class Gateway:
            async def generate(self, request):
                self.material = request.material
                return {'status':'COMPLETE', 'text':'{"artifacts":[{"kind":"analysis","content":{"answer":"ok"}}]}'}
        gateway = Gateway()
        context = SimpleNamespace(read_evidence=lambda key:{'content':key})
        agent = SimpleNamespace(registry=SimpleNamespace(known_tools=set()), context_mgr=context)
        result = await AgentSubagentExecutor(agent).run(RoleTask(role=role('analysis', depends_on=('evidence',)),
            text='分析', batch_id=batch_id), SimpleNamespace(model_gateway=gateway, subagent_store=store))
        self.assertEqual(result.status, 'COMPLETE')
        self.assertEqual(gateway.material['prior_results']['evidence']['artifacts'][0]['content'], {'source':'actual'})

    async def test_file_read_role_uses_only_declared_tool(self):
        calls = []
        class Model:
            observes_native_requests = False
            async def chat_stream_with_tools(self, messages, tools=None, **options):
                calls.append(tools)
                if len(calls) == 1:
                    yield '', {'role':'assistant','content':'','tool_calls':[{'id':'read','function':{
                        'name':'file_read','arguments':json.dumps({'path':'/workspace/a.txt'})}}]}
                else:
                    yield '', {'role':'assistant','content':
                        '{"artifacts":[{"kind":"analysis","content":{"answer":"ok"}}]}'}
        class Registry:
            known_tools = frozenset({'file_read','file_write','agent_batch_create'})
            def get_definitions(self, **kwargs):
                return [{'type':'function','function':{'name':name,'description':'','parameters':{}}}
                         for name in sorted(self.known_tools)]
            def create_turn(self):
                return None
            def call_limits(self):
                return {}
        class Router:
            models_runtime = object()
            async def dispatch_stream(self, name, params, session_id):
                if name != 'file_read':
                    raise AssertionError(name)
                yield {'event':'result','result':{'content':'actual','sha256':'a'*64}}
        parent = Agent(llm=Model(), router=Router(), registry=Registry(),
            audit=SimpleNamespace(record=lambda *args:None), context_mgr=ContextManager(),
            prompt_builder=SimpleNamespace(build=lambda:''), memory=None)
        result = await AgentSubagentExecutor(parent).run(RoleTask(
            role=role('analysis',tools=('file_read',)), text='读取', batch_id='batch'),
            SimpleNamespace(conversation_id='c'))
        self.assertEqual(result.artifacts[0].content, {'answer':'ok'})
        self.assertEqual(result.native_receipt['tools'][0]['name'], 'file_read')
        self.assertEqual([row['function']['name'] for row in calls[0]], ['file_read'])

    async def test_tool_role_preserves_failure_receipt_and_prior_tool(self):
        for native_status, expected in [('UNKNOWN','UNKNOWN'), ('TRUNCATED','TRUNCATED'),
                                        ('COMPLETE','FAILED')]:
            with self.subTest(native_status=native_status):
                class Model:
                    observes_native_requests = True
                    calls = 0
                    async def chat_stream_with_tools(self, messages, tools=None, **options):
                        self.calls += 1
                        observe = options['request_observer']
                        observe('native_model_request', {'call_id':str(self.calls),
                            'parameters':{'max_tokens':393216}})
                        if self.calls == 1:
                            observe('native_model_result', {'result_status':'COMPLETE',
                                'finish_reason':'tool_calls'})
                            yield '', {'role':'assistant','content':'','tool_calls':[{'id':'read',
                                'function':{'name':'file_read','arguments':'{"path":"/workspace/a.txt"}'}}]}
                        else:
                            observe('native_model_result', {'result_status':native_status,
                                'http_status':503 if native_status == 'UNKNOWN' else 200,
                                'visible_content_chars':0, 'terminal_received':native_status != 'UNKNOWN'})
                            raise RuntimeError('真实失败，不重试')
                class Registry:
                    known_tools = frozenset({'file_read'})
                    def get_definitions(self, **kwargs):
                        return [{'type':'function','function':{'name':'file_read','parameters':{}}}]
                    def call_limits(self): return {}
                class Router:
                    async def dispatch_stream(self, name, params, session_id):
                        yield {'event':'result','result':{'content':'actual'}}
                parent = Agent(llm=Model(), router=Router(), registry=Registry(),
                    audit=SimpleNamespace(record=lambda *args:None), context_mgr=ContextManager(),
                    prompt_builder=SimpleNamespace(build=lambda:''), memory=None)
                result = await AgentSubagentExecutor(parent).run(RoleTask(
                    role=role('analysis',tools=('file_read',)), text='读取', batch_id='batch'),
                    SimpleNamespace(conversation_id='c'))
                self.assertEqual(result.status, expected)
                self.assertEqual(len(result.native_receipt['calls']), 2)
                self.assertEqual(result.native_receipt['model']['model_params']['max_tokens'],393216)
                self.assertEqual(result.native_receipt['tools'][0]['result']['content'],'actual')
                self.assertEqual(parent.llm.calls, 2)

    async def test_finance_callback_follows_current_research_not_first_binding(self):
        from tests.subagent_fixtures import MemoryStore
        class Research:
            active = False
            record = {}
            def role_batch_summary(self, batch_id):
                return None
            async def record_role_result(self, batch_id, result):
                self.active = True
                self.recorded = (batch_id, result['role_id'])
        research = Research()
        class Executor:
            async def run(self, task, context):
                research.active = True
                research.record = {'research_contract_version':3}
                return complete_result(task.role.role_id)
        context = ExecutionContext('default', Path('.'), Path,
            subagent_executor=Executor(), subagent_store=MemoryStore(), research=research,
            subagent_extension=FinanceSubagentExtension(SimpleNamespace(research=research)))
        transport = SubagentTransport({'finance_evidence':role('finance_evidence')}).bind(context)
        created = await transport.call_tool('agent_batch_create', {
            'tasks':[{'role_id':'finance_evidence','task':'取证'}]}, 'c')
        result = await transport.call_tool('agent_batch_result', {'batch_id':created['batch_id']}, 'c')
        self.assertTrue(result['complete'])
        self.assertEqual(research.recorded, (created['batch_id'], 'finance_evidence'))

    async def test_tool_source_is_registered_in_parent_research(self):
        class Model:
            observes_native_requests = False
            def __init__(self):
                self.calls = 0
            async def chat_stream_with_tools(self, messages, tools=None, **options):
                self.calls += 1
                if self.calls == 1:
                    yield '', {'role':'assistant','content':'','tool_calls':[{'id':'read','function':{
                        'name':'web_fetch','arguments':json.dumps({'url':'https://example.org'})}}]}
                else:
                    yield '', {'role':'assistant','content':json.dumps({'artifacts':[{
                        'kind':'source_excerpts','content':{'excerpts':[]}}]})}
        class Registry:
            known_tools = frozenset({'web_fetch'})
            def get_definitions(self, **kwargs):
                return [{'type':'function','function':{'name':'web_fetch','description':'','parameters':{}}}]
            def call_limits(self):
                return {}
        class Research:
            active = True
            def __init__(self):
                self.observed = []
            async def save_source(self, result):
                result.pop('_source_file')
                result['source_id'] = 'Sactual'
                return result
            async def observe(self, event):
                self.observed.append(event)
        research = Research()
        class Router:
            def __init__(self):
                self.research = research
                self.params = []
            async def dispatch_stream(self, name, params, session_id):
                self.params.append(params)
                yield {'event':'result','result':{'url':'https://example.org',
                    'content':'actual source', '_source_file':{'base64':'raw'}}}
        parent = Agent(llm=Model(), router=Router(), registry=Registry(),
            audit=SimpleNamespace(record=lambda *args:None), context_mgr=ContextManager(),
            prompt_builder=SimpleNamespace(build=lambda:''), memory=None)
        result = await AgentSubagentExecutor(parent).run(RoleTask(
            role=role('finance_evidence',tools=('web_fetch',),kind='source_excerpts'), text='取证'),
            SimpleNamespace(conversation_id='c', subagent_extension=
                FinanceSubagentExtension(SimpleNamespace(research=research))))
        self.assertEqual(result.status, 'COMPLETE')
        self.assertEqual(result.native_receipt['tools'][0]['result']['source_id'], 'Sactual')
        self.assertTrue(research.observed[0]['subagent'])
        self.assertTrue(parent.router.params[0]['include_file'])
        self.assertIs(parent.router.research, research)


if __name__ == '__main__':
    unittest.main()
