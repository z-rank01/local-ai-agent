"""Authenticated public-only specialist transport; business validation stays in stock."""
from __future__ import annotations
import asyncio

import json
import httpx
from fastapi import HTTPException
from core.research_stream import ResearchStreamError

DUTIES = {'C01', 'C02', 'C03', 'C04'}
PRIVATE_KEYS = {'account', 'account_id', 'cash', 'lots', 'holdings', 'orders', 'fills',
                'user_material', 'user_materials', 'private', 'api_key', 'credentials',
                'card', 'cards', 'portfolio'}
ROOT_FIELDS = {'as_of', 'material', 'evidence_ids', 'analysis_contract', 'validation_feedback'}


async def rejection_metadata(response):
    """Classify an explicit rejection without retaining provider text or echoed input."""
    from core.research_errors import classify_rejection
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


_active_nodes = {}
_stopped_jobs = set()


async def stop_job(job_id):
    """Cancel the supplier socket, while allowing the stock HTTP caller to save UNKNOWN."""
    import asyncio
    _stopped_jobs.add(job_id)
    tasks = [task for (job, _), task in tuple(_active_nodes.items()) if job == job_id]
    for task in tasks:
        task.cancel()
    if tasks:
        await asyncio.gather(*tasks)
    return {'job_id': job_id, 'cancelled_nodes': len(tasks)}


async def generate(runtime, body):
    import asyncio
    import re
    association = body.get('node') if isinstance(body, dict) else None
    if association is None:
        return await _generate(runtime, body)
    if (not isinstance(association, dict) or set(association) != {'job_id', 'request_id'} or
            any(not isinstance(v, str) or not re.fullmatch(r'[\w:.-]{1,160}', v) for v in association.values())):
        raise HTTPException(422, '内部任务关联无效')
    key = (association['job_id'], association['request_id'])
    if key in _active_nodes or key[0] in _stopped_jobs:
        raise HTTPException(409, '任务已停止或节点仍在执行，不自动重发')
    task = asyncio.create_task(_generate(runtime, {k:v for k,v in body.items() if k != 'node'},
                                        request_id='finance:' + json.dumps(key, separators=(',', ':'))))
    _active_nodes[key] = task
    try:
        return await task
    finally:
        _active_nodes.pop(key, None)


async def _generate(runtime, body, *, request_id=None):
    if isinstance(body,dict) and 'execution_profile' in body:
        profile=body['execution_profile']
        from core.model_execution import resolve_profile
        from harness.contracts.models import ModelRequest
        from harness.runtime.model_gateway import ConversationModelGateway
        from harness.infrastructure.model_ledger import RecordedRequest
        import uuid
        try:
            spec,_=resolve_profile(runtime,profile)
        except PermissionError as exc:
            raise HTTPException(403,str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(409,str(exc)) from exc
        if spec['kind']=='cloud': validate_public(body['material'])
        try:
            answer=await ConversationModelGateway(runtime, profile).generate(ModelRequest(
                request_id or uuid.uuid4().hex, body['prompt'], body['material']))
        except RecordedRequest as exc:
            raise HTTPException(409, {'message': str(exc), 'code': 'MODEL_REQUEST_RECORDED',
                                     'ledger_call_id': exc.call_id, 'recorded_status': exc.status,
                                     'input_conflict': exc.conflict}) from exc
        except asyncio.CancelledError:
            answer={'status':'UNKNOWN','text':'','usage':None,'usage_status':'NOT_REPORTED',
                    'error':'REQUEST_CANCELLED'}
        return {**answer,'execution_hash':body['execution_hash']}
    raise HTTPException(409, '旧模型流程仅可查看，请从会话创建新研究')
