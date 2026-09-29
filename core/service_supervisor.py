"""Client for the loopback supervisor hosted by the launcher process.

The daily BFF cannot access Docker Desktop's named pipe from its process tree.
This client sends only fixed, allow-listed lifecycle operations to the launcher,
which also starts the stock backend as a top-level process.
"""
from __future__ import annotations

from pathlib import Path

import httpx

from . import config

SUPERVISOR_URL = 'http://127.0.0.1:9511'
TOKEN_PATH = config._PROJECT_ROOT / 'data' / 'private' / 'service-supervisor.token'


async def run_operation(operation: str, *, timeout: float = 180.0) -> dict:
    try:
        token = TOKEN_PATH.read_text(encoding='utf-8').strip()
    except OSError as exc:
        raise RuntimeError('本机服务控制器未启动，请通过启动.bat重新启动服务') from exc
    if not token:
        raise RuntimeError('本机服务控制器凭据为空，请通过启动.bat重新启动服务')

    try:
        async with httpx.AsyncClient(timeout=timeout, trust_env=False) as client:
            response = await client.post(
                SUPERVISOR_URL + '/api/operation',
                headers={'Authorization': 'Bearer ' + token},
                json={'operation': operation},
            )
    except httpx.HTTPError as exc:
        raise RuntimeError('本机服务控制器不可用，请退出后通过启动.bat重新启动服务') from exc

    try:
        result = response.json()
    except ValueError:
        result = {}
    if response.is_error:
        detail = result.get('detail') if isinstance(result, dict) else None
        raise RuntimeError(detail or f'本机服务控制器返回 HTTP {response.status_code}')
    if not isinstance(result, dict) or not result.get('ok'):
        raise RuntimeError('本机服务控制器返回了无效结果')
    return result
