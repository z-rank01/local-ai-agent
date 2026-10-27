"""Framework-neutral model/tool loop with provider-owned turn policies."""
from __future__ import annotations

from contextlib import aclosing
import asyncio
import json
import logging
import re
import time
from dataclasses import dataclass, field
from typing import Any, AsyncGenerator, Protocol, runtime_checkable

from .audit_logger import AuditLogger
from .context_manager import ContextManager
from .llm_client import LLMClient, strip_think_tags_from_history
from .prompt_builder import PromptBuilder
from .tool_registry import ToolRegistry
from .tool_router import ToolRouter

logger = logging.getLogger('core.agent')


@dataclass(slots=True)
class AgentEvent:
    """Structured event emitted by the model/tool loop."""
    kind: str
    text: str = ''
    data: dict[str, Any] = field(default_factory=dict)


@runtime_checkable
class MemoryHooks(Protocol):
    async def fetch_workspace_context(self, session_id: str, *, conversation_key: str | None = None) -> list[str]: ...
    async def ensure_memory_scaffold(self, session_id: str, conversation_key: str, title: str) -> None: ...
    async def update_memory_after_turn(self, session_id: str, conversation_key: str, title: str, messages: list[dict]) -> None: ...
    def derive_conversation_title(self, messages: list[dict]) -> str: ...


def _format_prefetch_content(fname: str, content: str, max_chars: int) -> str:
    total = len(content)
    if max_chars <= 0 or total <= max_chars:
        return f'【文件: {fname}】\n{content}'
    head = content[:max_chars]
    return (f'【文件: {fname}】（本页 {max_chars}/{total} 字符）\n{head}\n'
            f'...[本页到此为止，文件共 {total} 字符，尚未读完]'
            f'\n请用 file_read(path="/workspace/.../{fname}", offset={max_chars}) 继续读取剩余内容；'
            '读完之前不要把本页当作完整文件下结论。')


_MAX_PREFETCH_FILES = 3
_PREFETCH_UNLIMITED = 10 ** 9


def _prefetch_budget() -> int:
    from . import config
    if not getattr(config, 'PREFETCH_ENABLED', True):
        return 0
    value = getattr(config, 'PREFETCH_MAX_CHARS', 0)
    return _PREFETCH_UNLIMITED if value is None or value <= 0 else value


def _format_tool_result_preview(result: Any, max_chars: int = 1600) -> str:
    try:
        text = json.dumps(result, ensure_ascii=False, default=str, indent=2)
    except Exception:
        text = str(result)
    if len(text) <= max_chars:
        return text
    return text[:max_chars] + f'\n...[已截断，仅显示前 {max_chars} 字符，共 {len(text)} 字符]'


def tool_failed(value):
    return isinstance(value, dict) and (bool(value.get('error')) or value.get('success') is False
        or value.get('exit_code', 0) != 0 or value.get('status') == 'failed'
        or tool_failed(value.get('result')))


def model_projection(value):
    if isinstance(value, dict):
        if 'model_observation' in value:
            return model_projection(value['model_observation'])
        return {k: model_projection(v) for k, v in value.items() if k not in ('local_result', 'local_attachments')}
    if isinstance(value, list):
        return [model_projection(v) for v in value]
    return value


def unfinished_execution(reply: str, user_request: str) -> bool:
    if re.search(r'仅.*(?:讨论|代码|示例)|只.*(?:讨论|代码|示例)|不要执行|不用执行|代码示例|example|do not (?:run|execute)', user_request, re.I):
        return False
    prose = re.sub(r'```[\s\S]*?```', '', reply)
    return bool(re.search(r'```(?:python|py|bash|sh|powershell|javascript)\s', reply, re.I)
                and re.search(r'让我|我先|接下来.*(?:读取|执行|运行|查看|分析)|let me|I will|I.ll', prose, re.I)
                and not re.search(r'尚未执行|未运行|无法执行|不能执行|示例|not executed|cannot run', prose, re.I))


class Agent:
    """Run normal chat or delegate orchestration to the registered turn policy."""

    def __init__(self, *, llm: LLMClient, router: ToolRouter, registry: ToolRegistry,
                 audit: AuditLogger, context_mgr: ContextManager, prompt_builder: PromptBuilder,
                 memory: MemoryHooks | None = None, tool_tier: str = 'all', max_rounds: int = 0) -> None:
        self.llm = llm
        self.router = router
        self.registry = registry
        self.audit = audit
        self.context_mgr = context_mgr
        self.prompt_builder = prompt_builder
        self.memory = memory
        self.tool_tier = tool_tier
        self.max_rounds = max_rounds
        self.allow_tools = True
        self.tool_descriptions: dict[str, str] = {}

    def derive(self, target):
        """Initialize a related executor with frozen dependencies and independent turn configuration."""
        target.__init__(llm=self.llm, router=self.router, registry=self.registry,
            audit=self.audit, context_mgr=self.context_mgr, prompt_builder=self.prompt_builder,
            memory=self.memory, tool_tier=self.tool_tier, max_rounds=self.max_rounds)
        for name in ('allow_tools', 'workspace_cloud_allowed', 'cloud_write_allowed',
                     'turn', 'run_control', 'turn_reply_purpose_enabled', 'turn_loop_enabled',
                     'turn_window_seconds', 'quality_first_research', 'continuing_checkpoint'):
            if hasattr(self, name):
                setattr(target, name, getattr(self, name))
        target.tool_descriptions = dict(getattr(self, 'tool_descriptions', {}))
        target.extra_system_sections = list(getattr(self, 'extra_system_sections', []))
        if hasattr(self, 'allowed_tool_names'):
            target.allowed_tool_names = set(self.allowed_tool_names) if self.allowed_tool_names is not None else None
        if hasattr(self, 'evidence_history'):
            target.evidence_history = list(self.evidence_history)
        return target

    async def run(self, messages: list[dict], session_id: str = 'default',
                  conversation_key: str | None = None) -> AsyncGenerator[AgentEvent, None]:
        from . import config
        from harness.runtime.turns import NullTurn
        import uuid

        conversation_key = conversation_key or session_id
        messages = strip_think_tags_from_history(messages)
        turn = getattr(self, 'turn', None)
        if turn is None:
            turn = self.registry.create_turn() if hasattr(self.registry, 'create_turn') else NullTurn()
        self.turn = turn
        turn.start(getattr(self, 'evidence_history', messages))
        policy_stream = turn.stream(self, messages, session_id, conversation_key)
        if policy_stream is not None:
            async with aclosing(policy_stream) as events:
                async for event in events:
                    yield event
            return

        cloud = getattr(self.llm, 'cloud', False)
        try:
            ws_sections = []
            if self.allow_tools and self.memory and not cloud and not getattr(self, 'continuing_checkpoint', False):
                try:
                    title = self.memory.derive_conversation_title(messages)
                    await self.memory.ensure_memory_scaffold(session_id, conversation_key, title)
                    ws_sections = await self.memory.fetch_workspace_context(session_id, conversation_key=conversation_key)
                except Exception:
                    pass
            if not messages or messages[0].get('role') != 'system':
                messages.insert(0, {'role': 'system', 'content': self.prompt_builder.build(
                    extra_sections=ws_sections + list(getattr(self, 'extra_system_sections', [])))})
            workspace_granted = getattr(self, 'workspace_cloud_allowed', config.WORKSPACE_CLOUD_ALLOWED)
            if self.allow_tools and (not cloud or workspace_granted) and not getattr(self, 'continuing_checkpoint', False):
                try:
                    messages = await self._inject_context_into_messages(messages, session_id)
                except Exception:
                    logger.warning('Workspace prefetch skipped')
            messages = await self.context_mgr.process(messages)
            allow_write = bool(getattr(self, 'cloud_write_allowed', config.CLOUD_WRITE_ALLOWED))
            tool_defs = self.registry.get_definitions(tier=self.tool_tier, use_short_desc=True,
                allow_write=(not cloud) or allow_write)
            from harness.runtime.tool_context import describe_tools
            tool_defs = describe_tools(tool_defs, self.tool_descriptions)
            if not hasattr(self.router, 'models_runtime') or (
                    not cloud and getattr(self.llm, 'model', '').split(':')[0].lower() == 'gemma4'):
                tool_defs = [d for d in tool_defs if d['function']['name'] != 'local_agent']
            if getattr(self.context_mgr, 'reader', None) is not None:
                tool_defs.append({'type': 'function', 'function': {
                    'name': 'context_evidence_read',
                    'description': '分页回读本会话当前授权历史中的原始证据。用于核对压缩前的事实、反证和来源。',
                    'parameters': {'type': 'object', 'properties': {
                        'evidence_id': {'type': 'string'}, 'cursor': {'type': 'integer', 'minimum': 0}},
                        'required': ['evidence_id'], 'additionalProperties': False}}})
            allowed_tool_names = getattr(self, 'allowed_tool_names', None)
            if allowed_tool_names is not None:
                tool_defs = [d for d in tool_defs if d['function']['name'] in allowed_tool_names]
            if cloud and not workspace_granted:
                tool_defs = []
                messages[0]['content'] += '\n当前工作区工具未获云端授权，本轮没有可调用工具。可正常聊天。用户要求读取、列出、分析本地文件或执行代码时，先明确说明尚未执行，并询问是否愿意在顶部“模型设置”开启“允许云端读取当前工作区文件”（文件内容和工具结果可能发送到云端模型）。用户也可选择本地模型。不要声称正在查看或执行，不要编造文件内容，不要以未执行的代码代替任务完成；仅在用户要求代码示例时提供并标明未执行。聊天中的同意不能代替设置开关，必须由用户在界面操作。'
            if not self.allow_tools:
                tool_defs = []
                messages[0]['content'] += '\n本轮只使用已提供的会话材料生成回答，不调用工具、不预取工作区文件。材料不足时明确说明缺口，不声称已核对未提供的证据。'
            messages[0]['content'] += '\n执行任务必须通过本轮实际提供的工具调用，代码块本身不会执行。没有执行结果时不得声称已经读取、修改或生成文件。用户只要求讨论或只读时遵守该范围。'
            if cloud and getattr(self.llm, 'spec', {}).get('options', {}).get('enable_thinking', None) is False:
                messages[0]['content'] += '\n当前思考输出关闭。若用户明确要求开启思考模式，请询问是否愿意在顶部“模型设置”开启“思考输出”；不能声称已自行开启。普通任务无需为此打断。'

            counts = {}
            budgets = getattr(self.registry, 'call_limits', lambda: {})()
            user_request = next((m.get('content', '') for m in reversed(messages) if m.get('role') == 'user'), '')
            round_number, execution_retries = 0, 0
            execution_deadline = None
            while execution_deadline is not None or self.max_rounds <= 0 or round_number < self.max_rounds:
                if getattr(self, 'local_agent_unknown', False):
                    yield AgentEvent('error', text='本地Gemma4调用终态未知，已保留成果；不自动重发或切换模型。')
                    return
                active = [d for d in tool_defs if d['function']['name'] not in budgets
                          or budgets[d['function']['name']] <= 0
                          or counts.get(d['function']['name'], 0) < budgets[d['function']['name']]]
                discovery = turn.allowed_tools()
                if discovery is not None:
                    active = [d for d in active if d['function']['name'] in discovery]
                window_closed = execution_deadline is not None and time.monotonic() >= execution_deadline
                final_round = (execution_deadline is None and self.max_rounds > 0
                               and round_number == self.max_rounds - 1)
                if window_closed or final_round:
                    active = []
                    messages.append({'role': 'system', 'content':
                        '[本轮处理窗口已到，请根据已取得的结果回答，明确剩余缺口。]' if window_closed else
                        '[本轮执行预算已到收尾阶段，请根据已取得的结果回答，明确剩余缺口。]'})
                self.audit.record('model_tools_offered', {'session_id': session_id,
                    'names': [d['function']['name'] for d in active],
                    'discovery_required': discovery is not None})
                request_options = {}
                if getattr(self.llm, 'observes_native_requests', False):
                    request_options['request_observer'] = lambda event, data: self.audit.record(event,
                        {'session_id': session_id, 'view_version': 'generic-turn-v1', **data})
                stream_source = self.llm.chat_stream_with_tools(messages, active or None, **request_options)
                control = getattr(self, 'run_control', None)
                if control:
                    await control.boundary(messages)
                    control.begin_step('模型请求')
                accumulated = None
                async with aclosing(stream_source) as stream:
                    async for token, message in stream:
                        if message is not None:
                            accumulated = message
                        elif token:
                            yield AgentEvent('token', text=token)
                if not accumulated:
                    raise RuntimeError('模型未返回完整消息，已取得的结果仍保留。')
                if control:
                    control.end_step()
                calls = accumulated.get('tool_calls') or []
                for call in calls:
                    if not call.get('id'):
                        call['id'] = 'call_' + uuid.uuid4().hex
                    call.setdefault('type', 'function')
                messages.append(accumulated)
                yield AgentEvent('message', data={'message': accumulated})
                if not calls:
                    reply = accumulated.get('content') or ''
                    if not reply:
                        raise RuntimeError('模型返回空回答，已有工具结果仍保留。')
                    if (not window_closed and tool_defs and self.allow_tools
                            and unfinished_execution(reply, user_request)):
                        if execution_retries >= 2 or not active or (
                                execution_deadline is None and self.max_rounds > 0
                                and round_number >= self.max_rounds - 1):
                            raise RuntimeError('模型只给出了待执行代码，没有完成所述操作。已有工具结果保留；代码未自动运行，任务尚未完成。')
                        execution_retries += 1
                        messages.append({'role': 'system', 'content': '执行检查：你刚才说要继续操作，却只输出了代码，代码块不会被执行。请根据用户原任务及只读/讨论等限制判断：若仍需且已获授权执行，使用实际工具调用继续；若用户只需要示例或存在阻碍，明确说明未执行及原因。不新增用户未授权的操作，不把代码示例当作已完成结果。'})
                        continue
                    if self.allow_tools and self.memory and not cloud:
                        try:
                            await self.memory.update_memory_after_turn(session_id, conversation_key,
                                self.memory.derive_conversation_title(messages), messages)
                        except Exception:
                            logger.warning('Local memory update failed')
                    yield AgentEvent('done', text=reply)
                    return
                if window_closed:
                    for call in calls:
                        skipped = {'role': 'tool', 'tool_call_id': call['id'],
                                   'tool_name': call['function']['name'],
                                   'content': '{"error":"本轮窗口已到，此步骤未执行。"}'}
                        messages.append(skipped)
                        yield AgentEvent('message', data={'message': skipped})
                    yield AgentEvent('error', text='本轮调用已达到上限；已完成的工具结果保留，请继续追问。')
                    return
                allowed = {d['function']['name'] for d in active}
                turn_boundary = False
                receipt_presentation = None
                async with aclosing(self._execute_tools(calls, session_id, messages, allowed=allowed,
                        budgets=budgets, counts=counts,
                        execution_deadline=execution_deadline)) as events:
                    async for event in events:
                        if event.kind == 'turn_boundary':
                            turn_boundary = True
                            continue
                        if len(calls) == 1 and event.kind == 'tool_end' and event.data.get('status') == 'ok':
                            policy_for = getattr(self.registry, 'execution_policy', lambda name: None)
                            policy = policy_for(event.data.get('name'))
                            presenter = getattr(policy, 'present_receipt', None)
                            if presenter:
                                receipt_presentation = presenter(event.data.get('name'),
                                    event.data.get('params') or {}, model_projection(event.data.get('result')),
                                    question=user_request, read_receipts=[])
                        yield event
                if receipt_presentation is not None:
                    message = {'role': 'assistant', 'content': receipt_presentation.text}
                    messages.append(message)
                    self.audit.record(receipt_presentation.audit_event, {'session_id': session_id,
                        'version': receipt_presentation.version, 'provider_call_added': False})
                    yield AgentEvent('token', text=receipt_presentation.text)
                    yield AgentEvent('message', data={'message': message})
                    yield AgentEvent('done', text=receipt_presentation.text)
                    return
                if turn_boundary:
                    execution_deadline = 0.0
                for call in calls:
                    name = call['function']['name']
                    counts[name] = counts.get(name, 0) + 1
                round_number += 1
            yield AgentEvent('error', text='本轮调用已达到上限；已完成的工具结果保留，请继续追问。')
        except Exception as exc:
            logger.error('Agent loop failed: %s', type(exc).__name__)
            yield AgentEvent('error', text=str(exc) or type(exc).__name__)

    async def run_sync(self, messages: list[dict], session_id: str = 'default',
                       conversation_key: str | None = None) -> tuple[str, list[dict]]:
        final_text = ''
        async for event in self.run(messages, session_id, conversation_key):
            if event.kind == 'token':
                final_text += event.text
            elif event.kind == 'done' and event.text:
                final_text = event.text
            elif event.kind == 'error':
                raise RuntimeError(event.text)
        return final_text, messages

    async def _execute_tools(self, tool_calls, session_id, messages, *, allowed=None,
                             budgets=None, counts=None, execution_deadline=None, phase='normal',
                             research_handoff=False, evidence_step=None, research_finalizing=False):
        from harness.runtime.turns import NullTurn
        turn = getattr(self, 'turn', None)
        if turn is None:
            turn = self.registry.create_turn() if hasattr(self.registry, 'create_turn') else NullTurn()
            self.turn = turn
        turn_boundary = False
        for tc in tool_calls:
            control = getattr(self, 'run_control', None)
            if control:
                await control.boundary(messages)
            fn = tc.get('function', {})
            name = fn.get('name', '')
            if turn_boundary or (execution_deadline is not None and time.monotonic() >= execution_deadline):
                skipped = {'role': 'tool', 'tool_call_id': tc['id'], 'tool_name': name,
                           'content': '{"error":"本轮处理窗口已到，此步骤未执行。"}'}
                messages.append(skipped)
                yield AgentEvent('message', data={'message': skipped})
                turn_boundary = True
                continue
            params, call_policy = {}, None
            start = time.monotonic()
            research = getattr(self.router, 'research', None)
            turn.research = research
            try:
                raw = fn.get('arguments', {})
                params = json.loads(raw) if isinstance(raw, str) else raw
                if not isinstance(params, dict):
                    raise ValueError('工具参数必须是 JSON 对象')
                self._validate_tool(name, params, research, research_finalizing)
                params = turn.before_tool(name, params)
                policy_for = getattr(self.registry, 'execution_policy', None)
                call_policy = policy_for(name) if policy_for else None
                if call_policy is not None:
                    call_policy.validate(name, params, phase='handoff' if research_handoff else phase)
                if allowed is not None and name not in allowed:
                    if budgets and name in budgets:
                        raise ValueError(f'当前步骤未开放 {name}；该请求未执行。请遵守当前交付或交接阶段，不能据此声称配额已用尽或没有检索结果。')
                    raise ValueError(f'当前步骤未开放 {name}；本步骤未执行，不代表供应商额度用完。')
                if control:
                    control.begin_step('工具：' + name)
                purpose = evidence_step.get('purpose', '') if evidence_step else ''
                yield AgentEvent('tool_start', data={'name': name, 'params': params, 'call_id': tc['id'], 'purpose': purpose})
                result = None
                async with aclosing(self._dispatch_tool(name, params, session_id, control, tc['id'], messages)) as events:
                    async for event in events:
                        if event.kind == 'tool_result':
                            result = event.data.get('result')
                        else:
                            yield event
                result = await turn.after_tool(name, result)
                if result is None:
                    raise RuntimeError('工具未返回最终执行结果')
                status = 'error' if tool_failed(result) else 'ok'
                if evidence_step is not None:
                    await research.complete_evidence(evidence_step, result, status)
                if control and not getattr(self, 'local_agent_unknown', False):
                    control.end_step()
            except Exception as exc:
                if evidence_step is not None and research.pending_evidence and research.pending_evidence[0] is evidence_step:
                    await research.complete_evidence(evidence_step, {'error': str(exc)}, 'error')
                if control:
                    import httpx
                    control.end_step(confirmed=not isinstance(exc, httpx.TransportError)
                        and '未返回最终执行结果' not in str(exc))
                result, status = self._tool_error(name, exc), 'error'
            observation = model_projection(result)
            content = json.dumps(observation, ensure_ascii=False, default=str)
            if budgets and counts is not None and name in budgets:
                remaining = {n: max(0, b - counts.get(n, 0) - (1 if n == name else 0))
                             for n, b in budgets.items() if b > 0}
                content += self._budget_note(remaining)
            message = {'role': 'tool', 'tool_call_id': tc['id'], 'tool_name': name, 'content': content}
            messages.append(message)
            yield AgentEvent('message', data={'message': message})
            from harness.runtime.receipts import call_summary
            yield AgentEvent('tool_end', text=self._tool_end_text(name, result, status) or call_summary(result, status), data={
                'name': name, 'call_id': tc['id'], 'params': params, 'status': status,
                'purpose': evidence_step.get('purpose', '') if evidence_step else '',
                'elapsed': time.monotonic() - start, 'result': result,
                'result_preview': _format_tool_result_preview(result)})
            self.audit.record('tool_loop', {'session_id': session_id, 'name': name, 'status': status})
            if call_policy is not None and call_policy.ends_window(name, result):
                turn_boundary = True
        if turn_boundary:
            yield AgentEvent('turn_boundary')

    def _validate_tool(self, name, params, research, research_finalizing):
        if research is not None and research.active and name not in research.tool_names:
            raise ValueError('自主研究当前步骤未开放此工具；先读取适用方法，并使用本轮已开放的研究工具。本步骤未执行。')

    async def _dispatch_tool(self, name, params, session_id, control, call_id, messages):
        result = None
        if name == 'context_evidence_read':
            result = self.context_mgr.read_evidence(**params)
        elif name == 'local_agent':
            self.router._policy.check(name, params)
            if hasattr(self.router, 'workspace_validate'):
                await self.router.workspace_validate()
            from .local_agent import delegate
            result = await delegate(self.router.models_runtime, self.router.execution_profile, params,
                self.context_mgr.read_evidence,
                lambda kind, data: self.audit.record(kind, {**data, 'research_phase': 'local_delegate'}))
            if result['status'] == 'UNKNOWN':
                self.local_agent_unknown = True
                if control:
                    control.end_step(confirmed=False)
        elif hasattr(self.router, 'dispatch_stream'):
            async with aclosing(self.router.dispatch_stream(name, params, session_id)) as stream:
                async for packet in stream:
                    if packet.get('event') == 'result':
                        result = packet['result']
                    else:
                        yield AgentEvent('tool_progress', data={'name': name, 'call_id': call_id, **packet})
        else:
            result = await self.router.dispatch(name, params, session_id)
        if result is None:
            raise RuntimeError('工具未返回最终执行结果')
        yield AgentEvent('tool_result', data={'result': result})

    def _tool_error(self, name, error):
        return {'error': str(error) or type(error).__name__}

    def _tool_end_text(self, name, result, status):
        return None

    def _budget_note(self, remaining):
        if not remaining:
            return ''
        return '\n[联网额度：' + '，'.join(f'{n} 还可 {r} 次' for n, r in remaining.items()) + '；请据此规划后续检索，额度用尽后基于已有结果回答。]'

    async def _inject_context_into_messages(self, messages: list[dict], session_id: str) -> list[dict]:
        user_content = next((m['content'] for m in reversed(messages) if m['role'] == 'user'), '')
        file_context = await self._prefetch_file_context(user_content, session_id)
        if not file_context:
            return messages
        last_user_idx = max(i for i, m in enumerate(messages) if m['role'] == 'user')
        new_messages = list(messages)
        new_messages[last_user_idx] = {**messages[last_user_idx], 'content':
            messages[last_user_idx]['content'] + '\n\n---\n[系统已预取以下文件的真实内容，请直接基于这些数据作答，不得虚构任何数字或设备名称]\n\n' + file_context}
        return new_messages

    async def _prefetch_file_context(self, user_content: str, session_id: str) -> str | None:
        if not hasattr(self.router, 'dispatch'):
            return None
        hints = ('workspace', 'data/', 'docs/', 'reports/', '文件', '文档', '报告', '数据')
        if not any(kw in user_content for kw in hints) and not re.search(r'\w+\.\w{2,5}\b', user_content):
            return None
        search_dirs = ['/workspace/data', '/workspace/docs', '/workspace/reports'] + [
            p for p in getattr(self.router, 'workspace_roots', []) if p != '/workspace']
        all_entries = []
        for dir_path in search_dirs:
            try:
                listing = await self.router.dispatch('file_list', {'directory': dir_path}, session_id)
                for entry in listing.get('entries', []):
                    if entry['type'] == 'file':
                        all_entries.append((dir_path, entry['name']))
            except Exception:
                continue
        target_files = []
        for dir_path, name in all_entries:
            if name in user_content:
                target_files.append((dir_path, name))
                continue
            for token in re.findall(r"['\u2018\u2019\u201c\u201d](.+?)['\u2018\u2019\u201c\u201d]", user_content):
                if name.startswith(token):
                    target_files.append((dir_path, name))
                    break
        remaining = _prefetch_budget()
        parts = []
        for dir_path, fname in target_files[:_MAX_PREFETCH_FILES]:
            if remaining <= 0:
                break
            try:
                result = await self.router.dispatch('file_read', {'path': f'{dir_path}/{fname}'}, session_id)
                if isinstance(result, dict) and result.get('unsupported'):
                    continue
                content = result.get('content', '')
                if not content:
                    continue
                parts.append(_format_prefetch_content(fname, content, remaining))
                remaining -= min(len(content), remaining)
            except Exception as exc:
                logger.warning('prefetch: could not read %s/%s: %s', dir_path, fname, exc)
        return '\n\n'.join(parts) if parts else None
