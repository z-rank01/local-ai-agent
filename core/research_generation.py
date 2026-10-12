"""Authenticated public-only specialist transport; business validation stays in stock."""
from __future__ import annotations

import json
import httpx
from fastapi import HTTPException
from .market_research import generation_parameters, _cloud_text_model
from .providers import read_key
from .research_stream import ResearchStreamError

DUTIES = {'C01', 'C02', 'C03', 'C04'}
PRIVATE_KEYS = {'account', 'account_id', 'cash', 'lots', 'holdings', 'orders', 'fills',
                'user_material', 'user_materials', 'private', 'api_key', 'credentials',
                'card', 'cards', 'portfolio'}
ROOT_FIELDS = {'as_of', 'material', 'evidence_ids', 'analysis_contract', 'validation_feedback'}


async def rejection_metadata(response):
    """Classify an explicit rejection without retaining provider text or echoed input."""
    from .research_errors import classify_rejection
    body = None
    try:
        await response.aread()
        body = response.json()
    except (httpx.HTTPError, ValueError, TypeError, AttributeError):
        pass  # The HTTP status already proves rejection, even if its body is unreadable.
    return classify_rejection(response.status_code, body, getattr(response, 'headers', {}))


def _public_cash_paths(packet):
    """Only company financial snapshots may use the public balance-sheet cash field."""
    import re
    paths = set()
    material = packet.get('material', {}) if isinstance(packet, dict) else {}
    if not isinstance(material, dict):
        return paths
    def financial(fin, symbol, base):
        if (not isinstance(fin, dict) or not isinstance(symbol, str) or
                not re.fullmatch(r'\d{6}', symbol) or fin.get('symbol') != symbol or
                not re.fullmatch('[0-9a-f]{64}', str(fin.get('snapshot_id', '')))):
            return False
        paths.add(base + ('latest', 'ytd', 'cash'))
        periods = fin.get('periods', [])
        if not isinstance(periods, list):
            raise HTTPException(422, '公开财务期间必须为列表')
        for index, _ in enumerate(periods):
            paths.add(base + ('periods', index, 'ytd', 'cash'))
        return True
    instrument = material.get('instrument', {})
    evidence = material.get('evidence', {})
    if isinstance(instrument, dict) and isinstance(evidence, dict) and financial(
            evidence.get('financial'), instrument.get('symbol'), ('material', 'evidence', 'financial')):
        paths.add(('material', 'metrics', 'financial', 'ytd', 'cash'))
    peers = material.get('peers', [])
    if not isinstance(peers, list):
        raise HTTPException(422, '公开同行材料必须为列表')
    for index, peer in enumerate(peers):
        if isinstance(peer, dict):
            financial(peer.get('financial'), peer.get('symbol'), ('material', 'peers', index, 'financial'))
    return paths


def validate_public(value, *, _path=(), _cash_paths=None):
    if _cash_paths is None:
        _cash_paths = _public_cash_paths(value)
    if isinstance(value, dict):
        if 'investment_rationale' in value:
            rationale = value['investment_rationale']
            if rationale is not None and (not isinstance(rationale, dict) or
                    rationale.get('status') != 'CONDITIONAL_RESEARCH_ONLY' or
                    set(rationale) - {'status', 'reason', 'counter', 'conditions', 'source_theme'}):
                raise HTTPException(422, '仅接受由公开研究生成的条件性研究理由')
        for key, child in value.items():
            path = _path + (key,)
            public_cash = (key == 'cash' and path in _cash_paths and
                           (child is None or type(child) in (int, float)))
            if str(key).lower() in PRIVATE_KEYS and not public_cash:
                raise HTTPException(422, '只读直连仅接受公开研究材料，私有字段须留在原授权通路')
            validate_public(child, _path=path, _cash_paths=_cash_paths)
    elif isinstance(value, list):
        for index, child in enumerate(value):
            validate_public(child, _path=_path + (index,), _cash_paths=_cash_paths)
    elif value is not None and not isinstance(value, (str, int, float, bool)):
        raise HTTPException(422, '研究材料类型无效')


async def generate(runtime, body):
    required = {'duty', 'provider', 'model', 'generation', 'prompt', 'material', 'execution_hash'}
    if not isinstance(body, dict) or not required <= set(body) or set(body) - required - {'transport'} or body['duty'] not in DUTIES:
        raise HTTPException(422, '只读职责请求无效')
    transport = body.get('transport')
    if transport is not None and (not isinstance(transport, dict) or
            set(transport) != {'stream', 'include_usage'} or transport['stream'] is not True or
            transport['include_usage'] is not True):
        raise HTTPException(422, '冻结传输配置不受支持')
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
    if transport is not None:
        payload.update(stream=True, stream_options={'include_usage': True})
    receipt = {'execution_hash': body['execution_hash'],
        'input_chars': sum(len(message['content']) for message in payload['messages']), 'model_params': {
        **params, 'provider': body['provider'], 'model': spec['model'],
        'response_format': payload['response_format'], 'enable_search': False, 'stream': payload['stream'],
        **({'stream_options': payload['stream_options']} if transport is not None else {})}}
    import hashlib
    input_hash = hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True,
                                          allow_nan=False).encode()).hexdigest()
    ledger = runtime.models.budget
    call_id = ledger.begin_research(body['provider'] + ':' + spec['model'],
                                   body['execution_hash'], input_hash, receipt['model_params'])
    finished = False
    progress_receipt = {}
    def progress(fields):
        progress_receipt.update(fields)
        ledger.finish_research(call_id, {**receipt, **progress_receipt, 'status': 'RUNNING'})
    def finish(result):
        nonlocal finished
        ledger.finish_research(call_id, result)
        finished = True
        return {**result, 'ledger_call_id': call_id}
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(1800, connect=10), trust_env=False) as client:
            url = spec['base_url'].rstrip('/') + '/chat/completions'
            if transport is not None:
                from .research_stream import read_completion
                async with client.stream('POST', url, headers={'Authorization': 'Bearer ' + key}, json=payload) as response:
                    if response.status_code in (400, 401, 403, 404, 422, 429):
                        return finish({**receipt, 'status': 'FAILED', 'error': 'PROVIDER_REJECTED',
                            **await rejection_metadata(response), 'text': '', 'usage': {}})
                    response.raise_for_status()
                    answer = await read_completion(response, progress)
            else:
                response = await client.post(url, headers={'Authorization': 'Bearer ' + key}, json=payload)
                if response.status_code in (400, 401, 403, 404, 422, 429):
                    return finish({**receipt, 'status': 'FAILED', 'error': 'PROVIDER_REJECTED',
                        **await rejection_metadata(response), 'text': '', 'usage': {}})
                response.raise_for_status()
                answer = response.json()
        choice = answer['choices'][0]
        reason, content = choice.get('finish_reason'), choice['message'].get('content')
        if reason not in ('stop', 'length') or not isinstance(content, str) or not answer.get('model'):
            raise ValueError('Missing terminal metadata')
        if answer['model'] != spec['model']:
            return finish({**receipt, 'status': 'FAILED', 'error': 'RESPONSE_MODEL_MISMATCH',
                    'text': '', 'response_model': answer['model'], 'response_id': answer.get('id'), 'usage': {}})
        usage = answer.get('usage') or {}
        return finish({**receipt, 'status': 'TRUNCATED' if reason == 'length' else 'COMPLETE',
                'text': content, 'finish_reason': reason, 'response_model': answer['model'],
                'response_id': answer.get('id'), 'usage_status': 'REPORTED' if usage else 'NOT_REPORTED', 'usage': {k: usage[k] for k in
                    ('prompt_tokens', 'completion_tokens', 'total_tokens') if type(usage.get(k)) is int}})
    except (httpx.HTTPError, ValueError, KeyError, IndexError, TypeError) as exc:
        if isinstance(exc, ResearchStreamError):
            category = exc.category
        elif isinstance(exc, httpx.TimeoutException):
            category = 'TRANSPORT_TIMEOUT'
        elif isinstance(exc, httpx.RemoteProtocolError):
            category = 'TRANSPORT_PROTOCOL'
        elif isinstance(exc, httpx.HTTPStatusError):
            category = 'HTTP_UNCONFIRMED_FAILURE'
        elif isinstance(exc, httpx.HTTPError):
            category = 'TRANSPORT_FAILURE'
        else:
            category = 'RESPONSE_INVALID_STRUCTURE'
        return finish({**receipt, **progress_receipt, 'status': 'UNKNOWN', 'text': '', 'usage': {},
                'error': 'NO_VERIFIED_TERMINAL_RECEIPT', 'failure_category': category})
    finally:
        if not finished:
            finish({**receipt, **progress_receipt, 'status': 'UNKNOWN', 'error': 'REQUEST_INTERRUPTED', 'usage': {}})
