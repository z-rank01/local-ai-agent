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

_REFERENCE_RE = re.compile(r'^[0-9a-f]{64}$')


def _account_action(_params):
    return {'action': 'view', 'kind': 'account'}


def _find_action(params):
    action = {'action': 'find', 'kind': 'research'}
    for key in ('symbol', 'page'):
        if key in params and params[key] is not None:
            action[key] = params[key]
    return action


def _candidate_find_action(params):
    action = {'action': 'find', 'kind': 'scan'}
    if params.get('page') is not None:
        action['page'] = params['page']
    return action


def _market_brief_action(params):
    return {'action': 'submit', 'kind': 'market_brief',
            **({'refresh': True} if params.get('refresh') is True else {})}


def _market_brief_find_action(params):
    action = {'action': 'find', 'kind': 'market_brief'}
    if params.get('page') is not None:
        action['page'] = params['page']
    return action


def _theme_research_find_action(params):
    action = {'action': 'find', 'kind': 'theme_research'}
    if params.get('page') is not None:
        action['page'] = params['page']
    return action


def _track_review_find_action(params):
    action = {'action': 'find', 'kind': 'track_review'}
    if params.get('page') is not None:
        action['page'] = params['page']
    return action


def _track_find_action(params):
    return {'action': 'track_find',
            **({'search': params['search']} if params.get('search') else {}),
            **({'page': params['page']} if params.get('page') is not None else {})}


def _track_follow_action(params):
    return {'action': 'track_follow', 'kind': params.get('kind'),
            'reference': {'type': 'task', 'token': params.get('reference', '')},
            **({'search': params['name']} if params.get('name') else {}),
            **({'symbol': params['symbol']} if params.get('symbol') else {})}


def _track_reference_action(action_name):
    def build(params):
        return {'action': action_name,
                'reference': {'type': 'track', 'token': params.get('reference', '')},
                **({'page': params['page']} if action_name == 'track_read' and params.get('page') is not None else {})}
    return build


def _theme_research_action(params):
    return {'action': 'theme_research',
            'reference': {'type': 'task', 'token': params.get('reference', '')},
            **({'event_id': params['event_id']} if params.get('event_id') else {})}


def _report_action(params):
    return {'action': 'view', 'kind': 'job',
            'reference': {'type': 'task', 'token': params.get('reference', '')},
            **({'page': params['page']} if params.get('page') is not None else {})}


def _public_evidence_find_action(params):
    return {'action': 'public_evidence_find',
            'reference': {'type': 'task', 'token': params.get('reference', '')},
            **({'search': params['search']} if params.get('search') else {}),
            **({'report_page': params['report_page']} if params.get('report_page') is not None else {}),
            **({'cursor': params['cursor']} if params.get('cursor') else {})}


def _public_evidence_read_action(params):
    return {'action': 'public_evidence_read',
            'reference': {'type': 'task', 'token': params.get('reference', '')},
            'event_id': params.get('event_id'),
            **({'cursor': params['cursor']} if params.get('cursor') else {})}


def _submit_action(params):
    action = {'action': 'submit', 'kind': 'research'}
    mode = params.get('research_mode')
    if mode:
        action['research_mode'] = mode
    name = params.get('name')
    if isinstance(name, str) and name.strip():
        # The broker resolves the name to a code through its trusted name table;
        # the model only supplies the entity the user actually said.
        action['reference'] = {'type': 'candidate_name', 'name': name.strip()}
    symbol = params.get('symbol')
    if symbol is not None:
        action['symbol'] = symbol
    return action


def _screen_action(params):
    action = {'action': 'submit', 'kind': 'scan'}
    if params.get('filters'):
        action['filters'] = params['filters']
    return action


def _reference_action(action_name):
    def build(params):
        return {'action': action_name, 'reference': {'type': 'task', 'token': params.get('reference', '')}}
    return build


_TOOL_ACTIONS = {
    'stock_account_view': _account_action,
    'stock_research_find': _find_action,
    'stock_candidate_find': _candidate_find_action,
    'stock_market_brief_find': _market_brief_find_action,
    'stock_theme_research_find': _theme_research_find_action,
    'stock_track_review_find': _track_review_find_action,
    'stock_track_find': _track_find_action,
    'stock_track_follow': _track_follow_action,
    'stock_track_ignore': _track_reference_action('track_ignore'),
    'stock_track_read': _track_reference_action('track_read'),
    'stock_theme_research_submit': _theme_research_action,
    'stock_report_read': _report_action,
    'stock_public_evidence_find': _public_evidence_find_action,
    'stock_public_evidence_read': _public_evidence_read_action,
    'stock_research_submit': _submit_action,
    'stock_candidate_screen': _screen_action,
    'stock_market_brief': _market_brief_action,
    'stock_market_brief_continue': _reference_action('continue_market_brief'),
    'stock_research_cancel': _reference_action('cancel_research'),
    'stock_research_resume': _reference_action('resume_research'),
}

_SYMBOL_RE = re.compile(r'^\d{6}$')
_RESEARCH_MODES = {'reuse', 'continue', 'redo'}
_REFERENCE_TOOLS = {'stock_report_read', 'stock_research_cancel', 'stock_research_resume',
                    'stock_market_brief_continue', 'stock_public_evidence_find',
                    'stock_public_evidence_read', 'stock_theme_research_submit',
                    'stock_track_follow', 'stock_track_ignore', 'stock_track_read'}


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

    def set_turn(self, conversation_id: str, request_id: str | None, query: str = '',
                 method_id: str = 'auto') -> None:
        """Open the per-turn context. Call once per accepted user message."""
        self._turns[conversation_id] = {
            'request_id': request_id or f'turn:{uuid.uuid4().hex}',
            'query': (query or '（股票只读查询）')[:16000],
            'method_id': method_id,
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
                return {'error': 'reference 必须是本会话 stock_research_find、stock_candidate_find 或 stock_market_brief_find 返回的 64 位任务引用；请先查找任务。'}
        if tool == 'stock_report_read' and params.get('page') is not None and (
                type(params['page']) is not int or params['page'] < 1):
            return {'error': '报告页码必须是从 1 开始的整数。'}
        if tool == 'stock_research_submit':
            symbol = params.get('symbol')
            name = params.get('name')
            if symbol and name:
                return {'error': 'symbol 和 name 只能二选一：给 6 位股票代码或用户说出的股票名称。'}
            if not symbol and not name:
                return {'error': '必须提供 symbol（6 位股票代码）或 name（股票名称）之一；用户只说名称时用 name 原样传入。'}
            multi_separators = ('/', '\\', '、', '，', ',', '；', ';')
            if name is not None:
                cleaned = name.strip()
                if any(sep in cleaned for sep in multi_separators):
                    return {'error': '一次只能提交一个标的：name 只填一个股票名称。要研究多只股票，请对每只分别调用一次本工具（名称必须出现在用户本轮消息中）。'}
            if symbol is not None and isinstance(symbol, str):
                if any(sep in symbol for sep in multi_separators) or ' ' in symbol:
                    return {'error': '一次只能提交一个标的：symbol 只填一个 6 位代码。要研究多只股票，请对每只分别调用一次本工具。'}
            if name is not None and (not isinstance(name, str) or not name.strip() or len(name) > 40):
                return {'error': 'name 必须是不超过 40 字的股票名称。'}
            if symbol is not None and (not isinstance(symbol, str) or not _SYMBOL_RE.match(symbol)):
                return {'error': 'symbol 必须是 6 位股票代码（如 600150）；不知道代码时请改用 name 传股票名称。'}
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
            return {'error': ('旧版请求的工具步数已满。' if state['boundary'] == 'STEP_LIMIT' else
                              '本轮处理窗口已到。') + '已取得的结果保留，请基于已有结果回答，后续可在下一轮继续。',
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
        # The broker classifies refusals the user can act on and sends a
        # pre-vetted sentence with them; when present it is the whole reason, so
        # prefer it over the generic guidance below.
        guidance = str(step.get('guidance') or '')
        reason_code = str(step.get('reason_code') or '')
        if status in ('STEP_LIMIT', 'TIME_LIMIT'):
            # Boundary responses carry no tool/code/context keys by contract.
            state['boundary'] = status
            return {'error': ('旧版请求的工具步数已满。' if status == 'STEP_LIMIT' else
                              '本轮处理窗口已到。') + '已取得的结果保留，请基于已有结果回答，后续可在下一轮继续。',
                    'status': status}
        if status in ('INVALID_TOOL', 'RULE_BLOCKED') or guidance:
            code = reason_code or step.get('code') or status
            if guidance:
                # State the reason and tell the model not to invent a workaround.
                message = f'股票工具未执行：{guidance}请如实说明原因，不要重试相同参数。'
            elif status == 'INVALID_TOOL':
                message = '股票工具未执行：参数未通过校验。请修正参数后重试，不要重试相同参数。'
            else:
                message = f'股票工具未执行（{code}），本机未提供可转述的原因；请如实说明未能执行，不要重试相同参数。'
            return {'error': message, 'status': status, 'code': code,
                    'reason_code': reason_code}
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
                observation = {'code': step['code'], 'note': '该结果没有模型可见字段；详细内容仅在本地附件展示，长报告可能分页。'}
        if tool == 'stock_market_brief_find' and isinstance(observation, dict) and observation.get('kind') == 'task_search':
            status_labels = {'SUCCEEDED': '可读取', 'QUEUED': '排队中', 'RUNNING': '运行中',
                             'FAILED': '失败', 'CANCELLED': '已取消'}
            observation = dict(observation)
            observation['items'] = [
                {'reference': item.get('reference'), 'created_at': item.get('created_at'),
                 'execution': status_labels.get(item.get('status'), '状态待核对')}
                for item in (observation.get('items') or []) if isinstance(item, dict)
            ]
            observation['display_scope'] = ('任务引用只用于下一次内部报告读取，不向用户展示。'
                                            '回答只比较已读取版本的日期、资讯范围、来源失败记录和分析情况。')
        if tool == 'stock_report_read' and isinstance(observation, dict) and observation.get('kind') == 'market_brief':
            page = params.get('page', 1)
            if page > 1:
                # The stock broker's public projection summarizes the whole
                # brief, while the requested page is delivered locally at turn
                # end.  Do not let the model treat that summary as page text.
                observation = {
                    'kind': 'market_brief_page', 'requested_page': page,
                    'detail_available': 'YES' if step.get('local_result_available') else 'NO',
                    'page_reading': '第 {page} 页正文已作为本地附件交给用户，模型未读取附件正文。'
                                    '如需解释页内事件，先核对该页与公开事件的映射，再逐事件读取原文证据；'
                                    '未核对前不要列出页内事件，'
                                    '无法确认映射时明确说明。'.format(page=page),
                }
            else:
                observation = dict(observation)
                # The public event preview describes the whole brief, not this
                # report page.  Keep page comparisons tied to the dedicated
                # evidence finder, which can confirm page-to-event mappings.
                observation.pop('events', None)
                target_date = observation.pop('target_date', None)
                if isinstance(target_date, str):
                    observation['trading_day_description'] = f'行情锚定的最近交易日：{target_date}。'
                news_start = observation.pop('news_start', None)
                news_end = observation.pop('news_end', None)
                if isinstance(news_start, str) and isinstance(news_end, str):
                    observation['news_window_description'] = (
                        f'资讯核对区间：{news_start} 至 {news_end}；资讯核对截止日：{news_end}。')
                failed_dates = observation.pop('failed_dates', None)
                if isinstance(failed_dates, list):
                    dates = [day for day in failed_dates if isinstance(day, str)]
                    observation['announcement_directory_coverage'] = (
                        '东方财富公告目录失败日期：' + ('、'.join(dates) if dates else '未记录') +
                        ('。' if dates else '。本版未记录失败，不代表旧版失败已补采或全市场公告全部覆盖。'))
                status_labels = {
                    'OK': '本次事件分析已完成；不代表全市场公告全部覆盖',
                    'PARTIAL': '本次事件分析部分完成，仍有缺口；不能据此判断两版缺口相同，详情见本地报告',
                    'MODEL_PENDING_REVIEW': '模型结果待核查；详情见本地报告',
                    'BUDGET_EXHAUSTED': '分析因预算边界未完成；详情见本地报告',
                }
                analysis_status = observation.pop('analysis_status', None)
                if analysis_status in status_labels:
                    observation['analysis_status_description'] = status_labels[analysis_status]
                observation['requested_page'] = page
                observation['evidence_scope'] = ('模型只读到版本元信息，未读取本地附件正文；不得声称已读报告正文。'
                                                 '此摘要没有页内事件清单；不得从报告读取结果比较第 1 页事件。'
                                                 '若需解释或对比页内事件，先核对报告页与公开事件的映射，'
                                                 '再逐事件读取原文证据。'
                                                 '无法确认映射时只比较已核对的版本元信息。')
                observation['attachment_scope'] = ('本地附件只包含本次请求的第 1 页；整份报告可能分页。'
                                                   '不要说全部正文已在这一页；如需后续页，继续读取下一页。')
                observation['source_scope'] = ('公告失败日期只对应本项目的东方财富公告 API 目录，'
                                               '不是证监会或交易所网站的公告接口；'
                                               '发改委、证监会文章是独立来源。'
                                               '对比版本时只陈述可核对的状态与原文，不推测差异原因。'
                                               '旧版有失败日期而新版未记录，只能说明各版本状态，'
                                               '不能断言旧问题已经解决。'
                                               '面向用户用中文名称和日期列表说明，不展示字段名或 JSON 数组；'
                                               '不能核对报告顺序时不要猜测序号。')
                observation['interpretation_scope'] = (
                    '行情锚定的最近交易日不是报告生成或发布日期；'
                    '资讯核对截止日已在中文摘要中给出；'
                    '报告版本时间须按查找结果中的创建时间说明。'
                    '读三个报告页可能只涉及两个版本，应按版本数量表述。'
                    '公司公告中的自查、声明或预计只能归因于公司，未有独立来源时不可写成已核实事实。'
                    '用户未要求时不要显示任务引用哈希或工具调用名。')
        return {
            'model_observation': observation,
            'status': status,
            'code': step.get('code', ''),
            'broker_continue': step.get('continue', True),
            'local_result_available': bool(step.get('local_result_available')),
            'broker_sequence': step.get('sequence'),
        }

    async def finish_turn(self, conversation_id: str) -> list[dict]:
        """Finish the turn and return local results keyed to broker step numbers."""
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
        results = resp.get('local_results', [])
        return [item for item in results if isinstance(item, dict)
                and isinstance(item.get('sequence'), int)
                and isinstance(item.get('text'), str) and item['text']]

    async def close(self) -> None:
        await self._client.aclose()

    async def _prepare(self, state: dict, conversation_id: str) -> None:
        resp = await self._post('/api/master/broker/prepare', {
            'query': state['query'], 'actor': ACTOR, 'conversation': conversation_id,
            'request_id': state['request_id'], 'method_id': state['method_id'],
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
