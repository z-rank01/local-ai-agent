"""Stock read-only bridge — adapts the frozen IN2 tools to the paper broker gateway.

One broker session per user turn: lazily prepared on the first stock tool call,
one step per call, finished when the turn ends. Identity (actor/conversation),
tickets and step sequences stay inside this adapter; the model only supplies
business parameters. See docs/工具网关.md "IN2：local-ai-agent 只读接入契约"
in the stock repository (frozen 2026-09-07).
"""

from __future__ import annotations

import logging
import re
import uuid

import httpx

logger = logging.getLogger("core.stock_bridge")

ACTOR = 'local-ai-agent'

_LOCAL_RESULT_MARKER = '#### 本轮本地结果'
_DETAIL_SEPARATOR = '\n\n---\n\n'
_REFERENCE_RE = re.compile(r'^[0-9a-f]{64}$')


def _account_action(_params):
    return {'action': 'view', 'kind': 'account'}


def _find_action(params):
    action = {'action': 'find', 'kind': 'research'}
    for key in ('symbol', 'page'):
        if key in params and params[key] is not None:
            action[key] = params[key]
    return action


def _report_action(params):
    return {'action': 'view', 'kind': 'job',
            'reference': {'type': 'task', 'token': params.get('reference', '')}}


def _submit_action(params):
    action = {'action': 'submit', 'kind': 'research'}
    mode = params.get('research_mode')
    if mode:
        action['research_mode'] = mode
    reference = params.get('reference')
    if reference:
        action['reference'] = {'type': 'task', 'token': reference}
    symbol = params.get('symbol')
    if symbol is not None:
        action['symbol'] = symbol
    return action


def _reference_action(action_name):
    def build(params):
        return {'action': action_name, 'reference': {'type': 'task', 'token': params.get('reference', '')}}
    return build


_TOOL_ACTIONS = {
    'stock_account_view': _account_action,
    'stock_research_find': _find_action,
    'stock_report_read': _report_action,
    'stock_research_submit': _submit_action,
    'stock_research_cancel': _reference_action('cancel_research'),
    'stock_research_resume': _reference_action('resume_research'),
}

_SYMBOL_RE = re.compile(r'^\d{6}$')
_RESEARCH_MODES = {'reuse', 'continue', 'redo'}
_REFERENCE_TOOLS = {'stock_report_read', 'stock_research_cancel', 'stock_research_resume'}


class StockBridge:
    """Per-turn broker session manager for the read-only stock tools."""

    def __init__(self, base_url: str, token: str, *, client: httpx.AsyncClient | None = None):
        self._base_url = base_url.rstrip('/')
        self._client = client or httpx.AsyncClient(timeout=httpx.Timeout(130.0, connect=10.0))
        if token:
            self._client.headers['Authorization'] = f'Bearer {token}'
        # conversation_id -> turn state; turns on one conversation are serialized
        # by the BFF's exclusive_turn guard, so a plain dict is race-free.
        self._turns: dict[str, dict] = {}

    def set_turn(self, conversation_id: str, request_id: str | None, query: str = '') -> None:
        """Open the per-turn context. Call once per accepted user message."""
        self._turns[conversation_id] = {
            'request_id': request_id or f'turn:{uuid.uuid4().hex}',
            'query': (query or '（股票只读查询）')[:16000],
            'ticket': None,
            'sequence': 0,
            'finished': False,
        }

    def has_open_turn(self, conversation_id: str) -> bool:
        state = self._turns.get(conversation_id)
        return bool(state and state['ticket'] and not state['finished'])

    async def call_tool(self, tool: str, params: dict, session_id: str) -> dict:
        """Map one frozen stock tool call to one broker step. Never raises."""
        if tool not in _TOOL_ACTIONS:
            return {'error': f'未知的股票工具: {tool}'}
        if tool in _REFERENCE_TOOLS:
            reference = params.get('reference', '')
            if not isinstance(reference, str) or not _REFERENCE_RE.match(reference):
                return {'error': f'reference 必须是本会话 stock_research_find 返回的 64 位任务引用；请先调用 stock_research_find 获取。'}
        if tool == 'stock_research_submit':
            symbol = params.get('symbol')
            reference = params.get('reference')
            if symbol and reference:
                return {'error': 'symbol 和 reference 只能二选一：新研究给 6 位代码，continue/redo 定向给本会话的 64 位任务引用。'}
            if reference is not None and (not isinstance(reference, str) or not _REFERENCE_RE.match(reference)):
                return {'error': 'reference 必须是本会话 stock_research_find 返回的 64 位任务引用；请先调用 stock_research_find 获取。'}
            if not reference and (not isinstance(symbol, str) or not _SYMBOL_RE.match(symbol)):
                return {'error': 'symbol 必填且必须是 6 位股票代码（如 600150）；research_mode 可选 reuse/continue/redo。'}
            mode = params.get('research_mode')
            if mode is not None and mode not in _RESEARCH_MODES:
                return {'error': 'research_mode 只能是 reuse（默认，已有则复用）/ continue（无旧任务不新建）/ redo（强制新建）。'}
        state = self._turns.get(session_id)
        if state is None or state['finished']:
            # Direct dispatch outside a BFF turn (tests, tooling).
            self.set_turn(session_id, None)
            state = self._turns[session_id]
        if state.get('boundary'):
            # A broker boundary is terminal for the turn; short-circuit locally
            # instead of letting our sequence counter desync from the server.
            return {'error': '本轮股票工具的步数或时间已达上限，已取得的结果保留，请基于已有结果回答。',
                    'status': state['boundary']}
        try:
            if state['ticket'] is None:
                await self._prepare(state, session_id)
            state['sequence'] += 1
            step = await self._post('/api/master/broker/step', {
                'ticket': state['ticket'], 'actor': ACTOR, 'conversation': session_id,
                'sequence': state['sequence'], 'tool_call': _TOOL_ACTIONS[tool](params),
            })
        except _BridgeError as exc:
            return {'error': str(exc)}
        status = step.get('status')
        if status in ('STEP_LIMIT', 'TIME_LIMIT'):
            # Boundary responses carry no tool/code/context keys by contract.
            state['boundary'] = status
            return {'error': '本轮股票工具的步数或时间已达上限，已取得的结果保留，请基于已有结果回答。', 'status': status}
        if status in ('INVALID_TOOL', 'RULE_BLOCKED'):
            code = step.get('code') or status
            return {'error': f'股票工具未执行（{code}）；请修正参数或换个思路，不要重试相同参数。',
                    'status': status, 'code': code}
        observation = step.get('observation') or {}
        if not observation:
            capsule = step.get('tool') or {}
            if capsule:
                # The broker's safe tool capsule is the acceptance receipt for
                # submit/cancel/resume (action/status/operation/symbol).
                observation = dict(capsule)
            elif step.get('code'):
                # Direct/local routes carry no model-visible projection; tell the model
                # the outcome code so it never has to guess from an empty object.
                observation = {'code': step['code'], 'note': '该结果没有模型可见字段；完整内容已作为本地附件直接展示给用户。'}
        return {
            'model_observation': observation,
            'status': status,
            'code': step.get('code', ''),
            'local_result_available': bool(step.get('local_result_available')),
        }

    async def finish_turn(self, conversation_id: str) -> list[str]:
        """Finish the turn's broker session and harvest local-only attachment texts.

        Returns attachment texts in step order (matching the calls whose results
        carried local_result_available). Safe to call with no session.
        """
        state = self._turns.pop(conversation_id, None)
        if not state or state['ticket'] is None or state['finished']:
            return []
        try:
            resp = await self._post('/api/master/broker/finish', {
                'ticket': state['ticket'], 'actor': ACTOR, 'conversation': conversation_id,
                'completion': '',
            })
        except _BridgeError as exc:
            logger.warning('stock bridge finish failed: %s', exc)
            return []
        reply = resp.get('reply', '')
        if _LOCAL_RESULT_MARKER not in reply:
            return []
        section = reply.split(_LOCAL_RESULT_MARKER, 1)[1].strip()
        return [part.strip() for part in section.split(_DETAIL_SEPARATOR) if part.strip()]

    async def close(self) -> None:
        await self._client.aclose()

    async def _prepare(self, state: dict, conversation_id: str) -> None:
        resp = await self._post('/api/master/broker/prepare', {
            'query': state['query'], 'actor': ACTOR, 'conversation': conversation_id,
            'request_id': state['request_id'],
        })
        state['ticket'] = resp['ticket']

    async def _post(self, path: str, payload: dict) -> dict:
        try:
            resp = await self._client.post(self._base_url + path, json=payload)
        except httpx.HTTPError as exc:
            raise _BridgeError(f'股票后台无法连接（{type(exc).__name__}）；请确认模拟盘服务已启动且 STOCK_BRIDGE_URL 配置正确。') from exc
        if resp.status_code == 401:
            raise _BridgeError('股票网关认证失败（401）：请核对 STOCK_BRIDGE_TOKEN 与模拟盘状态目录中的 .paper-master-token 一致。')
        if resp.status_code >= 400:
            try:
                detail = resp.json().get('error', resp.text[:200])
            except Exception:
                detail = resp.text[:200] or f'HTTP {resp.status_code}'
            raise _BridgeError(f'股票网关拒绝请求（HTTP {resp.status_code}）：{detail}')
        return resp.json()


class _BridgeError(Exception):
    pass
