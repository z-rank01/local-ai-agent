"""Core adapter executing one role with a scoped context and tool registry."""
from copy import copy
import asyncio
import json
from core.agent import Agent
from core.context_manager import ContextManager
from harness.contracts.models import ModelRequest
from harness.contracts.subagents import RoleResult
from harness.runtime.subagents.results import public, structured_result, visible_text
from harness.runtime.subagents.extensions import NullSubagentExtension
from harness.runtime.subagents.materials import read_material, role_prompt


class ScopedRegistry:
    def __init__(self, registry, allowed):
        self._registry = registry
        self.allowed = frozenset(allowed)

    @property
    def known_tools(self):
        return self.allowed

    def get_definitions(self, **kwargs):
        return [row for row in self._registry.get_definitions(**kwargs)
                if row['function']['name'] in self.allowed]

    def __getattr__(self, name):
        return getattr(self._registry, name)


class ObservedLLM:
    def __init__(self, client):
        self.client = client
        self.receipt = {}
        self.calls = []
        self.observes_native_requests = True

    def __getattr__(self, name):
        return getattr(self.client, name)

    async def chat_stream_with_tools(self, messages, tools=None, **options):
        parent = options.get('request_observer')
        def observe(event, data):
            if event == 'native_model_request':
                self.receipt = {'call_id':data.get('call_id'),
                    'local_attempt_id':data.get('local_attempt_id'),
                    'model':data.get('model'), 'model_params':data.get('parameters', {}),
                    'result_status':'UNKNOWN'}
            if event == 'native_model_result':
                self.receipt.update(data)
                self.calls.append(dict(self.receipt))
            if parent:
                parent(event, data)
        options['request_observer'] = observe
        async for item in self.client.chat_stream_with_tools(messages, tools, **options):
            yield item


class ProfileSubagentExecutor:
    def __init__(self, runtime, profile, read_evidence, observe=None):
        self.runtime = runtime
        self.profile = profile
        self.read_evidence = read_evidence
        self.observe = observe

    def _material(self, task):
        return {'task':task.text, 'evidence':[self.read_evidence(key) for key in task.evidence_ids]}

    async def run(self, task, context):
        from .model_execution import invoke
        answer = await invoke(self.runtime, self.profile, task.role.prompt, self._material(task),
                              observe=self.observe)
        return RoleResult(role_id=task.role.role_id, status=answer['status'],
            native_receipt={key:value for key,value in answer.items() if key != 'text'},
            error=answer.get('error', ''), raw_text=visible_text(answer.get('text', '')))


class AgentSubagentExecutor:
    def __init__(self, agent):
        self.agent = agent
        self.known_tools = agent.registry.known_tools

    def validate(self, tasks):
        for task in tasks:
            for key in task.evidence_ids:
                self.agent.context_mgr.read_evidence(key)
            missing = task.role.tools - self.known_tools
            if missing:
                raise PermissionError('角色所需能力本轮未启用：' + ','.join(sorted(missing)))

    def available_roles(self, fallback):
        registry = self.agent.registry
        return registry.available_subagent_roles() if hasattr(registry, 'available_subagent_roles') else fallback

    def _extension(self, context):
        return getattr(context, 'subagent_extension', None) or NullSubagentExtension()

    def _material(self, task, context):
        evidence = [read_material(self.agent.context_mgr.read_evidence, key) for key in task.evidence_ids]
        material = {'task': task.text, 'evidence': evidence}
        store = getattr(context, 'subagent_store', None)
        prior = {}
        if task.batch_id and store is not None:
            batch = store.get_batch(task.batch_id)
            prior = {role_id: {
                key: value for key, value in row.items()
                if key in ('status', 'artifacts', 'issues', 'result_ref')}
                for role_id, row in batch['results'].items() if role_id in task.role.depends_on}
        material.update(self._extension(context).material(task, prior))
        return material

    def _structured(self, task, text, *, status, native_receipt=None, error=''):
        try:
            return structured_result(task, text, status=status,
                native_receipt=native_receipt, error=error)
        except ValueError as exc:
            return RoleResult(role_id=task.role.role_id, status='FAILED', error=str(exc),
                native_receipt=native_receipt or {}, raw_text=visible_text(text))

    def _archive_material(self, material):
        archive = getattr(self.agent.context_mgr, 'archive', None)
        if archive is None:
            return None
        evidence_id = archive({'role':'user', 'record_kind':'subagent_input',
                               'content':json.dumps(material, ensure_ascii=False)})
        self.agent.context_mgr.evidence_ids.add(evidence_id)
        return evidence_id

    async def run(self, task, context):
        if not task.role.tools:
            material = self._material(task, context)
            material_ref = self._archive_material(material)
            answer = await context.model_gateway.generate(ModelRequest(
                request_id=task.batch_id + ':' + task.role.role_id,
                prompt=role_prompt(task.role), material=material, structured_output=True))
            return self._structured(task, answer.get('text', ''), status=answer['status'],
                native_receipt={**{key:value for key,value in answer.items() if key != 'text'},
                                'material_ref':material_ref},
                error=answer.get('error', ''))
        return await self._run_with_tools(task, context)

    async def _run_with_tools(self, task, context):
        role = task.role
        missing = role.tools - self.known_tools
        if missing:
            raise PermissionError('角色所需能力本轮未启用：' + ','.join(sorted(missing)))
        registry = ScopedRegistry(self.agent.registry, role.tools)
        observed = ObservedLLM(self.agent.llm)
        child = self.agent.derive(Agent.__new__(Agent))
        child.llm = observed
        child.registry = registry
        child.router = copy(self.agent.router)
        child.router._registry = registry
        child.context_mgr = ContextManager(
            context_window=self.agent.context_mgr.context_window,
            compact_threshold=self.agent.context_mgr._compact_threshold, llm=observed)
        child.context_mgr.archive = self.agent.context_mgr.archive
        child.context_mgr.reader = self.agent.context_mgr.reader
        child.context_mgr.summary_profile = self.agent.context_mgr.summary_profile
        child.context_mgr.summary_cache_reader = self.agent.context_mgr.summary_cache_reader
        child.context_mgr.evidence_ids = set(task.evidence_ids)
        child.allowed_tool_names = set(role.tools)
        extension = self._extension(context)
        child.turn = extension.tool_turn(task)
        child.memory = None
        child.run_control = None
        child.evidence_history = []
        child.extra_system_sections = []
        child.continuing_checkpoint = True  # Explicit role material, no ambient workspace prefetch.
        material = self._material(task, context)
        material_ref = self._archive_material(material)
        messages = [{'role':'system','content':role_prompt(role)},
                    {'role':'user','content':json.dumps(material,ensure_ascii=False)}]
        response, tools = None, []
        child.router.research = None
        def receipt():
            return {'model':observed.receipt, 'calls':observed.calls,
                    'tools':tools, 'material_ref':material_ref}
        try:
            async for event in child.run(messages, context.conversation_id + ':subagent:' + role.role_id):
                if event.kind == 'message':
                    response = event.data.get('message', response)
                elif event.kind == 'tool_end':
                    tools.append({key:public(event.data.get(key)) for key in
                                  ('name','call_id','status','result')})
                    await extension.observe_tool(task, event.data)
                elif event.kind == 'error':
                    native_status = observed.receipt.get('result_status')
                    return RoleResult(role_id=role.role_id,
                        status=native_status if native_status in ('UNKNOWN','TRUNCATED') else 'FAILED',
                        error=event.text, native_receipt=receipt())
        except asyncio.CancelledError:
            return RoleResult(role_id=role.role_id, status='UNKNOWN',
                error='父运行停止；未确认回执不自动重发', native_receipt=receipt())
        except Exception as exc:
            return RoleResult(role_id=role.role_id, status='FAILED',
                error=str(exc) or type(exc).__name__, native_receipt=receipt())
        if response is None:
            return RoleResult(role_id=role.role_id, status='FAILED',
                error='子Agent没有最终回答', native_receipt=receipt())
        return self._structured(task, response.get('content', ''), status='COMPLETE',
            native_receipt=receipt())


def subagent_bindings(agent):
    from .subagent_store import RunSubagentStore
    return {'subagent_executor':AgentSubagentExecutor(agent),
            'subagent_store':RunSubagentStore(agent.run_control, agent.context_mgr),
            'subagent_extension':agent.turn.subagent_extension()}
