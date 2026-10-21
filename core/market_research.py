"""Authenticated background call using the Web app's existing cloud credentials."""
from __future__ import annotations

import re
import secrets
import json
from datetime import datetime
from urllib.parse import urlparse

import httpx
from fastapi import HTTPException, Request

from . import config
from .providers import _NON_CHAT_MODEL, _probe_ollama_models, read_key
from .stock_service import stock_service

PUBLIC_HOSTS = {'www.csrc.gov.cn', 'www.ndrc.gov.cn', 'pdf.dfcfw.com', 'data.eastmoney.com'}
LOOKUP_HOSTS = {'csrc': 'www.csrc.gov.cn', 'ndrc': 'www.ndrc.gov.cn',
                'announcement': 'data.eastmoney.com'}
_SPECIALIZED_MODEL = re.compile(r'omni|realtime|livetranslate|(?:^|[-_/])(?:mt|vl)(?:[-_/]|$)', re.IGNORECASE)


def _cloud_text_model(spec):
    capabilities = spec.get('capabilities')
    return (spec.get('kind') == 'cloud' and
            (capabilities is None or 'text' in capabilities) and
            not _NON_CHAT_MODEL.search(spec.get('model', '')) and
            not _SPECIALIZED_MODEL.search(spec.get('model', '')))


def _event_schema():
    reference = {'type': 'array', 'items': {'type': 'string'}}
    thought = {'type': 'object', 'properties': {'text': {'type': 'string'}, 'evidence': reference},
               'required': ['text', 'evidence'], 'additionalProperties': False}
    event = {'type': 'object', 'properties': {
        'event_id': {'type': 'string'},
        'facts': {'type': 'array', 'items': {'type': 'object',
                  'properties': {'excerpt_id': {'type': 'string'}},
                  'required': ['excerpt_id'], 'additionalProperties': False}},
        'impact': thought, 'counter': thought, 'watch': thought,
        'gaps': {'type': 'array', 'items': {'type': 'string'}}},
        'required': ['event_id', 'facts', 'impact', 'counter', 'watch', 'gaps'],
        'additionalProperties': False}
    return {'type': 'object', 'properties': {'events': {'type': 'array', 'items': event}},
            'required': ['events'], 'additionalProperties': False}


def authorize(request: Request):
    if not request.client or request.client.host not in ('127.0.0.1', '::1', 'testclient'):
        raise HTTPException(403, '仅允许本机股票后台调用')
    try:
        _, state = stock_service.paths()
        expected = (state / '.paper-market-model-token').read_text(encoding='utf-8').strip()
    except (OSError, ValueError):
        raise HTTPException(503, '股票后台分析凭据不可用') from None
    token = request.headers.get('authorization', '').removeprefix('Bearer ')
    if not token or not secrets.compare_digest(token, expected):
        raise HTTPException(401, '简报分析凭据无效')


async def public_search(runtime, body):
    """Search only a trusted public stock identity; snippets are never evidence."""
    base = {'symbol', 'name', 'source'}
    expanded = base | {'purpose', 'terms'}
    paging = isinstance(body, dict) and 'cursor' in body
    fields = set(body) - {'cursor'} if isinstance(body, dict) else set()
    if not isinstance(body, dict) or fields not in (base, expanded, expanded | {'question'}):
        raise HTTPException(422, '公开搜索参数无效')
    cursor = _search_cursor(body['cursor']) if paging else {}
    symbol, name, source = body['symbol'], body['name'], body['source']
    purpose, terms = body.get('purpose', 'company_direct'), body.get('terms', [])
    question = body.get('question', '')
    if (not isinstance(question, str) or len(question) > 240 or
            re.search(r'https?://|file:|[A-Za-z]:[\\/]|[\x00-\x1f]', question)):
        raise HTTPException(422, '公开研究问题格式无效')
    public_theme = (purpose == 'industry_background' and source in ('csrc', 'ndrc', 'web')
                    and symbol == '' and name == '' and bool(terms))
    if (not public_theme and (not isinstance(symbol, str) or not re.fullmatch(r'\d{6}', symbol) or
            not isinstance(name, str) or not re.fullmatch(r'[\u4e00-\u9fffA-Za-z0-9（）()·]{2,40}', name)) or
            not isinstance(source, str) or source not in set(LOOKUP_HOSTS) | {'web'} or
            not isinstance(purpose, str) or purpose not in {'industry_background', 'company_direct', 'benefit_link'} or
            not isinstance(terms, list) or len(terms) > 4 or
            any(not isinstance(term, str) or
                not re.fullmatch(r'[\u4e00-\u9fffA-Za-z0-9（）()·-]{2,24}', term) for term in terms)):
        raise HTTPException(422, '仅可查询公开证券和核准来源')
    if 'web_search' not in runtime.tool_registry.known_tools:
        return {'status': 'DISABLED', 'urls': []}
    if any(re.search(r'私有|私人|我的(?:账户|持仓)|密钥|密码|账户余额|交易口令', term) for term in [*terms, question]):
        raise HTTPException(422, '公开证据检索不能包含账户或私有信息')
    host = LOOKUP_HOSTS.get(source, '')
    search_scope = host + '/notices/detail/' + symbol + '/' if source == 'announcement' else host
    query = ((question + ' ' if question else '') + ' '.join(terms) + (' ' + symbol + ' ' + name if purpose != 'industry_background' else '')
             if terms else f'{symbol} {name}') + (f' site:{search_scope}' if host else '')
    try:
        found = await runtime.router.dispatch('web_search', {'query': query, 'max_results': 12, **cursor},
                                              session_id='stock-public-research')
    except Exception:
        return {'status': 'UNAVAILABLE', 'urls': []}
    if paging and isinstance(found, dict) and found.get('status') == 'PAGE_CHANGED_OR_INVALID':
        return {'status': 'PAGE_CHANGED_OR_INVALID', 'urls': [], 'coverage': 'unknown',
                'next_cursor': {'page': cursor['page'], 'offset': 0}}
    if not isinstance(found, dict) or found.get('error'):
        return {'status': 'UNAVAILABLE', 'urls': []}
    if paging:
        # Old containers silently ignore unknown fields. Never accept that as paging.
        try:
            following = found.get('next_cursor')
            if (found.get('coverage') != 'provider_page_only' or
                    type(found.get('page')) is not int or found['page'] != cursor['page'] or
                    type(found.get('offset')) is not int or found['offset'] != cursor['offset'] or
                    not isinstance(found.get('page_hash'), str) or
                    not re.fullmatch(r'[0-9a-f]{64}', found['page_hash']) or
                    type(found.get('page_result_count')) is not int or found['page_result_count'] < 0 or
                    found['page_result_count'] < cursor['offset'] or
                    found.get('status') not in {'OK', 'PARTIAL', 'UNAVAILABLE', 'PAGE_EMPTY'} or
                    not isinstance(found.get('results'), list) or 'next_cursor' not in found):
                raise ValueError()
            if following is not None:
                _search_cursor(following)
                if not ((following['page'] == cursor['page'] and following['offset'] > cursor['offset']
                         and following['offset'] < found['page_result_count']
                         and following.get('page_hash') == found['page_hash']) or
                        (following['page'] == cursor['page'] + 1 and following['offset'] == 0
                         and found['page_result_count'] > 0)):
                    raise ValueError()
        except (ValueError, HTTPException):
            return {'status': 'PAGING_UNSUPPORTED_OR_INVALID', 'urls': [],
                    'coverage': 'unknown', 'next_cursor': None}
    rows = found.get('results')
    urls = []
    for row in rows if isinstance(rows, list) else []:
        url = row.get('url') or row.get('link') if isinstance(row, dict) else None
        parsed = urlparse(url) if isinstance(url, str) else None
        if (parsed and parsed.scheme == 'https' and (not host or parsed.hostname == host) and
                not parsed.username and not parsed.password and url not in urls):
            if not host:
                from .public_address import public_url
                try:
                    public_url(url)
                except ValueError:
                    continue
            if source == 'announcement' and not re.fullmatch(
                    r'/notices/detail/' + re.escape(symbol) + r'/AN\d+\.html', parsed.path):
                continue
            if source == 'announcement' and (parsed.query or parsed.fragment):
                continue
            urls.append(url)
    if paging:
        return {'status': found['status'], 'urls': urls,
                'coverage': 'provider_page_only', 'next_cursor': following,
                'page': found['page'], 'offset': found['offset'],
                'page_hash': found['page_hash'], 'page_result_count': found['page_result_count'],
                'accepted_url_count': len(urls),
                'pagination_support': found.get('pagination_support') if found.get('pagination_support') in
                                      {'SUPPORTED', 'PARTIAL', 'UNSUPPORTED', 'UNKNOWN'} else 'UNKNOWN',
                'non_paging_engines': [name for name in found.get('non_paging_engines', [])
                                      if isinstance(name, str) and re.fullmatch(r'[A-Za-z0-9 _.-]{1,80}', name)]
                                     if isinstance(found.get('non_paging_engines'), list) else [],
                'provider_failures': [list(row) for row in found.get('provider_failures', [])
                                      if isinstance(row, (list, tuple)) and len(row) == 2
                                      and all(isinstance(value, str) for value in row)]
                                     if isinstance(found.get('provider_failures', []), list) else [],
                'diagnostic_role': 'UNTRUSTED_PROVIDER_STATUS_NOT_RESEARCH_EVIDENCE'}
    return {'status': 'OK' if urls else 'NO_MATCH', 'urls': urls}


def _search_cursor(value):
    if (not isinstance(value, dict) or set(value) not in ({'page', 'offset'}, {'page', 'offset', 'page_hash'}) or
            type(value.get('page')) is not int or value['page'] < 1 or
            type(value.get('offset')) is not int or value['offset'] < 0 or
            ('page_hash' in value and (not isinstance(value['page_hash'], str) or
                                     not re.fullmatch(r'[0-9a-f]{64}', value['page_hash']))) or
            (value['offset'] > 0 and 'page_hash' not in value)):
        raise HTTPException(422, '公开搜索游标无效')
    return dict(value)


async def available_models(runtime, *, refresh=False):
    # Use the same provider catalog as conversation model selection.  Discovered
    # cloud chat models inherit the provider's existing key and compatible URL.
    specs = await runtime.models.catalog(refresh=refresh)
    cloud = [{'id': s['id'], 'provider': s['provider_id'],
              'provider_name': s['provider_name'], 'model': s['model'], 'kind': 'cloud',
              'status': 'configured' if read_key(s.get('api_key_env', '')) else 'missing_key'}
             for s in specs if _cloud_text_model(s)]
    names = await _probe_ollama_models(config.OLLAMA_BASE_URL) or []
    local = []
    async with httpx.AsyncClient(timeout=httpx.Timeout(5, connect=2), trust_env=False) as client:
        for name in names:
            if _NON_CHAT_MODEL.search(name):
                continue
            try:
                response = await client.post(config.OLLAMA_BASE_URL.rstrip('/') + '/api/show', json={'model': name})
                response.raise_for_status()
                if 'completion' not in response.json().get('capabilities', []):
                    continue
            except (httpx.HTTPError, ValueError):
                continue
            local.append({'id': 'ollama:' + name, 'provider': 'ollama',
                          'provider_name': 'Ollama', 'model': name, 'kind': 'local',
                          'status': 'configured'})
    return local + cloud


def _public_failure_receipt(item):
    """A narrow, public collection-status projection; never arbitrary backend JSON."""
    if item.get('source') != 'backend_retrieval_receipt' or item.get('url') is not None:
        return False
    try:
        spans = item['excerpts']
        if not isinstance(spans, list) or not spans:
            return False
        body = json.loads(''.join(span['text'] for span in spans))
        if (not isinstance(body, dict) or set(body) != {'interface', 'params', 'quality', 'fetched_at',
                'error', 'response_id', 'source', 'available_at'} or
                body['interface'] != 'stock_notice_report' or body['source'] != 'AKShare' or
                body['quality'] != 'FAILED' or not isinstance(body['params'], dict) or
                set(body['params']) != {'date', 'symbol'} or
                not isinstance(body['params']['date'], str) or not re.fullmatch(r'\d{8}', body['params']['date']) or
                not isinstance(body['params']['symbol'], str) or not re.fullmatch(r'(?:\d{6}|全部)', body['params']['symbol']) or
                body['error'] != '上游超时；本次请求已结束，可稍后重试' or
                not isinstance(body['response_id'], str) or not re.fullmatch(r'[0-9a-f]{64}', body['response_id'])):
            return False
        for key in ('fetched_at', 'available_at'):
            if not isinstance(body[key], str) or datetime.fromisoformat(body[key]).tzinfo is None:
                return False
        return True
    except (ValueError, KeyError, TypeError):
        return False


def public_material(material):
    if not isinstance(material, list) or len(material) != 1 or not isinstance(material[0], dict):
        raise HTTPException(422, '每次只能分析一个公开事件正文块')
    item = material[0]
    base_fields = {'event_id', 'title', 'published', 'source', 'url', 'excerpts'}
    if set(item) not in (base_fields, base_fields | {'chunk_findings'}):
        raise HTTPException(422, '公开证据字段无效')
    parsed = urlparse(item['url']) if isinstance(item['url'], str) else None
    ordinary = (parsed and parsed.scheme == 'https' and parsed.hostname in PUBLIC_HOSTS and
                not parsed.username and not parsed.password and item['source'] in ('policy', 'industry', 'announcement'))
    broker = (parsed and parsed.scheme == 'https' and parsed.hostname == 'static.cninfo.com.cn' and
              not parsed.username and not parsed.password and not parsed.query and not parsed.fragment and
              re.fullmatch(r'/finalpage/\d{4}-\d{2}-\d{2}/\d+\.PDF', parsed.path) and item['source'] == 'public_broker_review')
    if (not (ordinary or broker or _public_failure_receipt(item)) or
            not isinstance(item['title'], str) or len(item['title']) > 2000 or
            not isinstance(item['published'], str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}', item['published']) or
            not isinstance(item['event_id'], str) or not re.fullmatch(r'[A-Za-z0-9:]+', item['event_id'])):
        raise HTTPException(422, '公开事件来源无效')
    spans = item['excerpts']
    if not isinstance(spans, list) or not spans or len(spans) > 4000:
        raise HTTPException(422, '正文位置无效')
    for span in spans:
        if (not isinstance(span, dict) or set(span) != {'excerpt_id', 'text', 'location'} or
                not isinstance(span['excerpt_id'], str) or not re.fullmatch(r'[A-Za-z0-9:]+', span['excerpt_id']) or
                not isinstance(span['text'], str) or not span['text'] or len(span['text']) > 1200 or
                not isinstance(span['location'], str) or len(span['location']) > 100):
            raise HTTPException(422, '正文位置无效')
    if 'chunk_findings' in item:
        findings = item['chunk_findings']
        if not isinstance(findings, list) or not findings or len(findings) > 4000:
            raise HTTPException(422, '跨块分析材料无效')
        for finding in findings:
            if (not isinstance(finding, dict) or
                    set(finding) != {'facts', 'impact', 'counter', 'watch', 'gaps'} or
                    not isinstance(finding['facts'], list) or
                    not isinstance(finding['gaps'], list) or
                    any(not isinstance(gap, str) for gap in finding['gaps'])):
                raise HTTPException(422, '跨块分析材料无效')
            for fact in finding['facts']:
                if (not isinstance(fact, dict) or set(fact) != {'excerpt_id', 'quote'} or
                        not isinstance(fact['excerpt_id'], str) or not isinstance(fact['quote'], str)):
                    raise HTTPException(422, '跨块事实无效')
            for key in ('impact', 'counter', 'watch'):
                part = finding[key]
                if (not isinstance(part, dict) or set(part) != {'text', 'evidence'} or
                        not isinstance(part['text'], str) or not isinstance(part['evidence'], list) or
                        any(not isinstance(ref, str) for ref in part['evidence'])):
                    raise HTTPException(422, '跨块判断无效')
    return material


def generation_parameters(provider, model, value):
    raise HTTPException(410,'独立分析参数已停用，模型参数继承所属会话')


async def generate(runtime, provider, model, prompt, material, *, generation=None, execution_profile=None):
    if execution_profile is None:
        raise HTTPException(409,'独立简报模型已停用；请从所属会话创建任务')
    from .model_execution import resolve_profile, invoke
    spec,_=resolve_profile(runtime,execution_profile)
    if spec['kind']=='cloud': public_material(material)
    return await invoke(runtime,execution_profile,prompt,material)
