"""Authenticated public-only specialist transport; business validation stays in stock."""
from __future__ import annotations

import json
import httpx
from fastapi import HTTPException
from .market_research import generation_parameters, _cloud_text_model
from .providers import read_key

DUTIES = {'C01', 'C02', 'C03', 'C04'}
PRIVATE_KEYS = {'account', 'account_id', 'cash', 'lots', 'holdings', 'orders', 'fills',
                'user_material', 'user_materials', 'private', 'api_key', 'credentials',
                'card', 'cards', 'portfolio'}
ROOT_FIELDS = {'as_of', 'material', 'evidence_ids', 'analysis_contract', 'validation_feedback'}


def validate_public(value):
    if isinstance(value, dict):
        if 'investment_rationale' in value:
            rationale = value['investment_rationale']
            if rationale is not None and (not isinstance(rationale, dict) or
                    rationale.get('status') != 'CONDITIONAL_RESEARCH_ONLY' or
                    set(rationale) - {'status', 'reason', 'counter', 'conditions', 'source_theme'}):
                raise HTTPException(422, '仅接受由公开研究生成的条件性研究理由')
        if any(str(k).lower() in PRIVATE_KEYS for k in value):
            raise HTTPException(422, '只读直连仅接受公开研究材料，私有字段须留在原授权通路')
        for child in value.values():
            validate_public(child)
    elif isinstance(value, list):
        for child in value:
            validate_public(child)
    elif value is not None and not isinstance(value, (str, int, float, bool)):
        raise HTTPException(422, '研究材料类型无效')


async def generate(runtime, body):
    required = {'duty', 'provider', 'model', 'generation', 'prompt', 'material', 'execution_hash'}
    if not isinstance(body, dict) or set(body) != required or body['duty'] not in DUTIES:
        raise HTTPException(422, '只读职责请求无效')
    import re
    if not isinstance(body['execution_hash'], str) or not re.fullmatch('[0-9a-f]{64}', body['execution_hash']):
        raise HTTPException(422, '冻结执行标识无效')
    material = body['material']
    if (not isinstance(material, dict) or set(material) - ROOT_FIELDS or
            not isinstance(material.get('material'), dict) or not isinstance(material.get('evidence_ids'), list)):
        raise HTTPException(422, '研究材料封装无效')
    validate_public(material)
    prompt = body['prompt']
    if not isinstance(prompt, str) or not prompt.strip():
        raise HTTPException(422, '职责提示词为空')
    params = generation_parameters(body['provider'], body['model'], body['generation'])
    await runtime.models.catalog()
    try:
        spec, _ = runtime.models.resolve(body['provider'], body['model'])
    except ValueError:
        raise HTTPException(422, '冻结模型不可用') from None
    if not _cloud_text_model(spec):
        raise HTTPException(422, '本直连接口需要已配置的云端文本模型')
    key = read_key(spec.get('api_key_env', ''))
    if not key:
        raise HTTPException(422, '冻结模型尚未配置密钥')
    payload = {**params, 'model': spec['model'], 'enable_search': False, 'stream': False,
               'response_format': {'type': 'json_object'},
               'messages': [{'role': 'system', 'content': prompt},
                            {'role': 'user', 'content': json.dumps(material, ensure_ascii=False, allow_nan=False)}]}
    receipt = {'execution_hash': body['execution_hash'], 'model_params': {
        **params, 'provider': body['provider'], 'model': spec['model'],
        'response_format': 'json_object', 'enable_search': False}}
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(1800, connect=10), trust_env=False) as client:
            response = await client.post(spec['base_url'].rstrip('/') + '/chat/completions',
                headers={'Authorization': 'Bearer ' + key}, json=payload)
        if response.status_code in (400, 401, 403, 404, 422, 429):
            return {**receipt, 'status': 'FAILED', 'error': 'PROVIDER_REJECTED',
                    'http_status': response.status_code, 'text': '', 'usage': {}}
        response.raise_for_status()
        answer = response.json()
        choice = answer['choices'][0]
        reason, content = choice.get('finish_reason'), choice['message'].get('content')
        if reason not in ('stop', 'length') or not isinstance(content, str) or not answer.get('model'):
            raise ValueError('Missing terminal metadata')
        if answer['model'] != spec['model']:
            return {**receipt, 'status': 'FAILED', 'error': 'RESPONSE_MODEL_MISMATCH',
                    'text': '', 'response_model': answer['model'], 'response_id': answer.get('id'), 'usage': {}}
        usage = answer.get('usage') or {}
        return {**receipt, 'status': 'TRUNCATED' if reason == 'length' else 'COMPLETE',
                'text': content, 'finish_reason': reason, 'response_model': answer['model'],
                'response_id': answer.get('id'), 'usage': {k: usage[k] for k in
                    ('prompt_tokens', 'completion_tokens', 'total_tokens') if type(usage.get(k)) is int}}
    except (httpx.HTTPError, ValueError, KeyError, IndexError, TypeError):
        return {**receipt, 'status': 'UNKNOWN', 'text': '', 'usage': {},
                'error': 'NO_VERIFIED_TERMINAL_RECEIPT'}
