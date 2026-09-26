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


async def available_models(runtime):
    # Provider discovery lists audio, translation and experimental endpoints too.
    # Cloud research is enabled only by an explicit text entry in models.json.
    cloud = [{'id': s['id'], 'provider': s['provider_id'], 'model': s['model'], 'kind': 'cloud'}
             for s in runtime.models._static_specs
             if s['kind'] == 'cloud' and 'text' in s.get('capabilities', []) and
             read_key(s.get('api_key_env', ''))]
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
            local.append({'id': 'ollama:' + name, 'provider': 'ollama', 'model': name, 'kind': 'local'})
    return local + cloud


def public_material(material):
    if not isinstance(material, list) or len(material) != 1 or not isinstance(material[0], dict):
        raise HTTPException(422, '每次只能分析一个公开事件正文块')
    item = material[0]
    if set(item) != {'event_id', 'title', 'published', 'source', 'url', 'excerpts'}:
        raise HTTPException(422, '公开证据字段无效')
    parsed = urlparse(item['url']) if isinstance(item['url'], str) else None
    if (not parsed or parsed.scheme != 'https' or parsed.hostname not in PUBLIC_HOSTS or
            parsed.username or parsed.password or item['source'] not in ('policy', 'industry', 'announcement') or
            not isinstance(item['title'], str) or len(item['title']) > 300 or
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
    if spec['kind'] != 'cloud' or _NON_CHAT_MODEL.search(spec['model']):
        raise HTTPException(422, '云端分析只接受已配置的文本模型')
    if spec['id'] not in {s['id'] for s in runtime.models._static_specs
                          if s['kind'] == 'cloud' and 'text' in s.get('capabilities', [])}:
        raise HTTPException(422, '请先在模型目录登记该文本分析模型')
    key = read_key(spec.get('api_key_env', ''))
    if not key:
        raise HTTPException(422, '该云端模型未配置密钥')
    payload = {'model': spec['model'], 'messages': [
        {'role': 'system', 'content': prompt}, {'role': 'user', 'content': __import__('json').dumps(material, ensure_ascii=False)}],
        'stream': False, 'max_tokens': 4096, 'response_format': {'type': 'json_object'}}
    if spec['provider_id'] == 'qwen' and re.match(r'^qwen3\.(?:7|8)-(?:max|flash)', spec['model']):
        payload['response_format'] = {'type': 'json_schema', 'json_schema': {
            'name': 'market_event', 'strict': True, 'schema': _event_schema()}}
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(180, connect=10), trust_env=False) as client:
            response = await client.post(spec['base_url'].rstrip('/') + '/chat/completions',
                headers={'Authorization': 'Bearer ' + key}, json=payload)
            response.raise_for_status()
            answer = response.json()
        content = answer['choices'][0]['message']['content']
        if not isinstance(content, str) or not content:
            raise ValueError('empty content')
        usage = answer.get('usage') if isinstance(answer.get('usage'), dict) else {}
        return {'text': content, 'usage': {k: usage[k] for k in ('prompt_tokens', 'completion_tokens', 'total_tokens')
                                          if type(usage.get(k)) is int}}
    except (httpx.HTTPError, KeyError, IndexError, ValueError):
        raise HTTPException(502, '云端分析调用失败或返回无效内容；不会自动改用其他模型') from None
