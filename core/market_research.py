"""Authenticated background call using the Web app's existing cloud credentials."""
from __future__ import annotations

import re
import secrets
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
    if not isinstance(body, dict) or set(body) not in (base, expanded):
        raise HTTPException(422, '公开搜索参数无效')
    symbol, name, source = body['symbol'], body['name'], body['source']
    purpose, terms = body.get('purpose', 'company_direct'), body.get('terms', [])
    public_theme = (purpose == 'industry_background' and source in ('csrc', 'ndrc')
                    and symbol == '' and name == '' and bool(terms))
    if (not public_theme and (not isinstance(symbol, str) or not re.fullmatch(r'\d{6}', symbol) or
            not isinstance(name, str) or not re.fullmatch(r'[\u4e00-\u9fffA-Za-z0-9（）()·]{2,40}', name)) or
            not isinstance(source, str) or source not in LOOKUP_HOSTS or
            not isinstance(purpose, str) or purpose not in {'industry_background', 'company_direct', 'benefit_link'} or
            not isinstance(terms, list) or len(terms) > 4 or
            any(not isinstance(term, str) or
                not re.fullmatch(r'[\u4e00-\u9fffA-Za-z0-9（）()·-]{2,24}', term) for term in terms)):
        raise HTTPException(422, '仅可查询公开证券和核准来源')
    if 'web_search' not in runtime.tool_registry.known_tools:
        return {'status': 'DISABLED', 'urls': []}
    host = LOOKUP_HOSTS[source]
    query = (' '.join(terms) + (' ' + symbol + ' ' + name if purpose != 'industry_background' else '')
             if terms else f'{symbol} {name}') + f' site:{host}'
    try:
        found = await runtime.router.dispatch('web_search', {'query': query, 'max_results': 12},
                                              session_id='stock-public-research')
    except Exception:
        return {'status': 'UNAVAILABLE', 'urls': []}
    if not isinstance(found, dict) or found.get('error'):
        return {'status': 'UNAVAILABLE', 'urls': []}
    rows = found.get('results')
    urls = []
    for row in rows if isinstance(rows, list) else []:
        url = row.get('url') or row.get('link') if isinstance(row, dict) else None
        parsed = urlparse(url) if isinstance(url, str) else None
        if (parsed and parsed.scheme == 'https' and parsed.hostname == host and
                not parsed.username and not parsed.password and url not in urls):
            urls.append(url)
    return {'status': 'OK', 'urls': urls}


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


def public_material(material):
    if not isinstance(material, list) or len(material) != 1 or not isinstance(material[0], dict):
        raise HTTPException(422, '每次只能分析一个公开事件正文块')
    item = material[0]
    base_fields = {'event_id', 'title', 'published', 'source', 'url', 'excerpts'}
    if set(item) not in (base_fields, base_fields | {'chunk_findings'}):
        raise HTTPException(422, '公开证据字段无效')
    parsed = urlparse(item['url']) if isinstance(item['url'], str) else None
    if (not parsed or parsed.scheme != 'https' or parsed.hostname not in PUBLIC_HOSTS or
            parsed.username or parsed.password or item['source'] not in ('policy', 'industry', 'announcement') or
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


async def generate(runtime, provider, model, prompt, material):
    public_material(material)
    if not isinstance(prompt, str) or not prompt or len(prompt) > 10000:
        raise HTTPException(422, '分析提示词无效')
    await runtime.models.catalog()
    try:
        spec, _ = runtime.models.resolve(provider, model)
    except ValueError:
        raise HTTPException(422, '所选分析模型不可用') from None
    if not _cloud_text_model(spec):
        raise HTTPException(422, '云端分析只接受已配置的文本模型')
    key = read_key(spec.get('api_key_env', ''))
    if not key:
        raise HTTPException(422, '该云端模型未配置密钥')
    payload = {'model': spec['model'], 'messages': [
        {'role': 'system', 'content': prompt}, {'role': 'user', 'content': __import__('json').dumps(material, ensure_ascii=False)}],
        'stream': False}
    output_limit = spec.get('analysis_max_tokens')
    if type(output_limit) is int and output_limit > 0:
        payload['max_tokens'] = output_limit
    if spec['provider_id'] == 'qwen' and re.match(r'^qwen3\.(?:7|8)-(?:max|flash)', spec['model']):
        payload['response_format'] = {'type': 'json_schema', 'json_schema': {
            'name': 'market_event', 'strict': True, 'schema': _event_schema()}}
    elif spec['provider_id'] == 'qwen' and spec['model'] == 'qwen3.5-flash':
        payload['response_format'] = {'type': 'json_object'}
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(180, connect=10), trust_env=False) as client:
            response = await client.post(spec['base_url'].rstrip('/') + '/chat/completions',
                headers={'Authorization': 'Bearer ' + key}, json=payload)
            response.raise_for_status()
            answer = response.json()
        choice = answer['choices'][0]
        content = choice['message']['content']
        if not isinstance(content, str) or (not content and choice.get('finish_reason') != 'length'):
            raise ValueError('empty content')
        usage = answer.get('usage') if isinstance(answer.get('usage'), dict) else {}
        return {'status': 'TRUNCATED' if choice.get('finish_reason') == 'length' else 'COMPLETE',
                'text': content, 'finish_reason': choice.get('finish_reason'),
                'model_params': {'max_tokens': payload.get('max_tokens'),
                                 'response_format': payload.get('response_format', {}).get('type')},
                'usage': {k: usage[k] for k in ('prompt_tokens', 'completion_tokens', 'total_tokens')
                          if type(usage.get(k)) is int}}
    except (httpx.HTTPError, KeyError, IndexError, ValueError):
        raise HTTPException(502, '云端分析调用失败或返回无效内容；不会自动改用其他模型') from None
