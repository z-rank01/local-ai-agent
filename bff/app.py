"""FastAPI app exposing a stable frontend-facing protocol."""
from __future__ import annotations
import asyncio
import os
import signal
import subprocess
import time
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager, aclosing
from urllib.parse import quote
from fastapi import FastAPI, HTTPException, Query, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from core import config
from core.auto_exit import AutoExitState
from .deps import get_chat_service, get_runtime, shutdown_runtime
from .schemas import (
    ActivateMessageVersionRequest,
    AppStatus,
    ChatRequest,
    ConversationSummary,
    CreateConversationRequest,
    EditMessageRequest,
    MessageRecord,
    ModelInfo,
    ProviderInfo,
    RegenerateRequest,
    UIStreamEvent,
    UpdateConversationRequest,
    WorkspaceDeleteResponse,
    WorkspaceFilePreview,
    WorkspaceImportRequest,
    WorkspaceImportResponse,
    WorkspaceRestoreResponse,
    WorkspaceTrashResponse,
    WorkspaceTreeResponse,
    WorkspaceUploadResponse,
)
@asynccontextmanager
async def lifespan(_: FastAPI):
    runtime = get_runtime()
    if os.environ.get('DAILY_SERVICES') == '1':
        from core.stock_service import stock_service
        await stock_service.attach(runtime, False)
        if stock_service.settings['enabled']:
            try:
                await stock_service.start(runtime)
            except Exception:
                # An optional backend must not prevent the chat/control UI starting.
                import logging
                logging.getLogger(__name__).exception('Stock service startup failed')
    app.state.auto_exit = AutoExitState(
        enabled=config.AUTO_EXIT_ON_CLOSE,
        timeout_seconds=config.HEARTBEAT_TIMEOUT_SECONDS,
    )
    from core.skills_service import skill_switches
    await skill_switches.restore(runtime)
    watcher = asyncio.create_task(_auto_exit_watcher()) if config.AUTO_EXIT_ON_CLOSE else None
    try:
        yield
    finally:
        if watcher is not None:
            watcher.cancel()
        await shutdown_runtime()
app = FastAPI(
    title="Local AI Agent Frontend Adapter",
    version="0.1.0",
    lifespan=lifespan,
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=config.WEB_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
async def _stream_ndjson(
    events: AsyncGenerator[UIStreamEvent, None],
    *,
    conversation_id: str | None,
) -> StreamingResponse:
    first_event: UIStreamEvent | None = None
    try:
        first_event = await anext(events)
    except StopAsyncIteration:
        first_event = None
    async def generate():
        if first_event is not None:
            yield first_event.model_dump_json() + "\n"
        try:
            async for event in events:
                yield event.model_dump_json() + "\n"
        except Exception as exc:
            detail = exc.detail if hasattr(exc, "detail") else str(exc)
            yield UIStreamEvent(
                event="error",
                conversation_id=conversation_id,
                data={"message": str(detail)},
            ).model_dump_json() + "\n"
    async def managed():
        async with aclosing(events):
            async for item in generate():
                yield item
    return StreamingResponse(managed(), media_type="application/x-ndjson")
@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}
@app.get("/api/status", response_model=AppStatus)
async def status() -> AppStatus:
    return get_chat_service().app_status()
def require_local_control(request: Request):
    origin = request.headers.get('origin')
    if not _is_loopback_host(request.client.host if request.client else None) or origin not in config.WEB_ORIGINS:
        raise HTTPException(403, '服务控制仅允许本机 Web 页面')
@app.get('/api/admin/stock-service')
async def stock_service_status(request: Request):
    if not _is_loopback_host(request.client.host if request.client else None):
        raise HTTPException(403, '仅允许本机查看服务控制')
    from core.stock_service import stock_service
    return await stock_service.status()
@app.post('/api/admin/stock-service')
async def stock_service_action(request: Request):
    require_local_control(request)
    if get_chat_service()._active_conversations:
        raise HTTPException(409, '聊天正在执行，请等待本轮结束后再切换服务')
    from core.stock_service import stock_service
    try:
        body = await request.json()
        if not isinstance(body, dict):
            raise ValueError('服务操作必须为对象')
        return await stock_service.action(get_runtime(), body, lambda: bool(get_chat_service()._active_conversations))
    except (ValueError, OSError) as exc:
        raise HTTPException(409, str(exc) if isinstance(exc, ValueError) else '本地服务配置或凭据不可读') from exc
    except __import__('httpx').HTTPError as exc:
        raise HTTPException(503, '股票服务连接失败，请刷新状态') from exc
@app.get('/api/admin/skills')
async def skills_status(request: Request):
    if not _is_loopback_host(request.client.host if request.client else None):
        raise HTTPException(403, '仅允许本机查看技能开关')
    from core.skills_service import skill_switches
    return skill_switches.status(get_runtime())
@app.post('/api/admin/skills')
async def skills_toggle(request: Request):
    require_local_control(request)
    if get_chat_service()._active_conversations:
        raise HTTPException(409, '聊天正在执行，请等待本轮结束后再切换技能')
    from core.skills_service import skill_switches
    try:
        body = await request.json()
        if not isinstance(body, dict) or not isinstance(body.get('websearch'), bool):
            raise ValueError('请求体须为 {"websearch": true 或 false}')
        return await skill_switches.set_websearch(
            get_runtime(), body['websearch'],
            lambda: bool(get_chat_service()._active_conversations))
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(503, str(exc)) from exc

def _is_loopback_host(host: str | None) -> bool:
    if not host:
        return False
    normalized = host.removeprefix("::ffff:")
    return normalized in {"127.0.0.1", "::1", "localhost"}
def _find_pids_on_port(port: int) -> list[int]:
    """PIDs of processes listening on a loopback TCP port (Windows netstat; empty elsewhere)."""
    if os.name != 'nt':
        return []
    try:
        completed = subprocess.run(['netstat', '-ano', '-p', 'tcp'], capture_output=True, text=True, timeout=10)
    except Exception:
        return []
    pids = set()
    for line in completed.stdout.splitlines():
        parts = line.split()
        if len(parts) >= 5 and parts[0].upper().startswith('TCP') and parts[3].upper() == 'LISTENING':
            host, _, port_text = parts[1].rpartition(':')
            if port_text.isdigit() and int(port_text) == port and host in ('127.0.0.1', '0.0.0.0', '::1', '[::1]', '::', '[::]'):
                pids.add(int(parts[4]))
    return sorted(pids)
def _stack_shutdown_targets() -> dict[str, list[int]]:
    from urllib.parse import urlparse
    web_port = int(os.environ.get('WEB_DEV_PORT', '5173'))
    targets = {
        'bff': [os.getpid()],
        'web': [pid for pid in _find_pids_on_port(web_port) if pid != os.getpid()],
    }
    if config.STOCK_BRIDGE_URL:
        port = urlparse(config.STOCK_BRIDGE_URL).port or 80
        targets['stock-bridge'] = [pid for pid in _find_pids_on_port(port) if pid != os.getpid()]
    return targets
def schedule_stack_shutdown(targets: dict[str, list[int]]) -> None:
    async def terminate() -> None:
        await asyncio.sleep(0.5)
        for name in ('web', 'stock-bridge'):
            for pid in targets.get(name, []):
                try:
                    os.kill(pid, signal.SIGTERM)
                except (ProcessLookupError, PermissionError):
                    pass
        await asyncio.sleep(0.3)
        os.kill(os.getpid(), signal.SIGTERM)
    asyncio.create_task(terminate())
@app.post('/api/admin/shutdown-stack')
async def shutdown_stack(request: Request) -> dict:
    origin = request.headers.get('origin')
    if not _is_loopback_host(request.client.host if request.client else None) or (origin and origin not in config.WEB_ORIGINS):
        raise HTTPException(403, detail='仅允许本机 Web 停止服务')
    return await _request_stack_shutdown()
async def _request_stack_shutdown() -> dict:
    """Graceful full exit: stock backend, web leftovers, tool containers, then us."""
    targets = _stack_shutdown_targets()
    from core.stock_service import stock_service
    service_status = await stock_service.status()
    if service_status['online']:
        try:
            await stock_service.request('/api/control/action', {'action': 'shutdown'})
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc
    elif targets.get('stock-bridge'):
        raise HTTPException(409, '股票后台未接入安全关闭接口，请先在股票服务中暂停并关闭后再退出')
    # The backend shuts itself down after draining; never kill an arbitrary port owner.
    targets.pop('stock-bridge', None)
    schedule_stack_shutdown(targets)
    return {'stopping': {name: pids for name, pids in targets.items() if pids}}
@app.get("/api/models", response_model=list[ModelInfo])
async def list_models(refresh: int = 0) -> list[ModelInfo]:
    return await get_chat_service().list_models(refresh=bool(refresh))
@app.get("/api/providers", response_model=list[ProviderInfo])
async def list_providers(refresh: int = 0) -> list[ProviderInfo]:
    return await get_chat_service().list_providers(refresh=bool(refresh))
@app.get("/api/conversations", response_model=list[ConversationSummary])
async def list_conversations(
    limit: int = 50,
    offset: int = 0,
    query: str | None = None,
) -> list[ConversationSummary]:
    return get_chat_service().list_conversations(limit=limit, offset=offset, query=query)
@app.post("/api/conversations", response_model=ConversationSummary, status_code=201)
async def create_conversation(request: CreateConversationRequest) -> ConversationSummary:
    return get_chat_service().create_conversation(request.title, request.model)
@app.get("/api/conversations/{conversation_id}", response_model=ConversationSummary)
async def get_conversation(conversation_id: str) -> ConversationSummary:
    return get_chat_service().get_conversation(conversation_id)
@app.patch("/api/conversations/{conversation_id}", response_model=ConversationSummary)
async def update_conversation(
    conversation_id: str,
    request: UpdateConversationRequest,
) -> ConversationSummary:
    return get_chat_service().update_conversation_title(conversation_id, request.title)
@app.delete("/api/conversations/{conversation_id}", status_code=204)
async def delete_conversation(conversation_id: str) -> Response:
    get_chat_service().delete_conversation(conversation_id)
    return Response(status_code=204)
@app.get("/api/conversations/{conversation_id}/messages", response_model=list[MessageRecord])
async def list_messages(conversation_id: str) -> list[MessageRecord]:
    return get_chat_service().get_messages(conversation_id)
@app.delete("/api/conversations/{conversation_id}/messages/{message_id}", status_code=204)
async def delete_message(conversation_id: str, message_id: str) -> Response:
    get_chat_service().delete_message(conversation_id, message_id)
    return Response(status_code=204)
@app.post("/api/conversations/{conversation_id}/messages/{message_id}/edit")
async def edit_message(
    conversation_id: str,
    message_id: str,
    request: EditMessageRequest,
) -> StreamingResponse:
    service = get_chat_service()
    return await _stream_ndjson(
        service.edit_message_and_regenerate(
            conversation_id,
            message_id=message_id,
            content=request.content,
            provider_id=request.provider_id, model=request.model,
            request_id=request.request_id,
        ),
        conversation_id=conversation_id,
    )
@app.post("/api/conversations/{conversation_id}/regenerate")
async def regenerate_conversation(
    conversation_id: str, request: RegenerateRequest | None = None
) -> StreamingResponse:
    service = get_chat_service()
    payload = request or RegenerateRequest()
    return await _stream_ndjson(
        service.regenerate_chat(conversation_id, message_id=payload.message_id, provider_id=payload.provider_id, model=payload.model, request_id=payload.request_id),
        conversation_id=conversation_id,
    )
@app.get("/api/conversations/{conversation_id}/export")
async def export_conversation(conversation_id: str, format: str = "markdown") -> Response:
    content, filename, media_type = get_chat_service().export_conversation(conversation_id, format=format)
    sanitized = filename.replace('"', '')
    ascii_fallback = sanitized.encode("ascii", errors="ignore").decode("ascii") or "conversation-export"
    encoded = quote(sanitized)
    headers = {
        "content-disposition": (
            f"attachment; filename=\"{ascii_fallback}\"; filename*=UTF-8''{encoded}"
        )
    }
    return Response(content=content, media_type=media_type, headers=headers)
@app.post("/api/workspace/import-local-paths", response_model=WorkspaceImportResponse)
async def import_local_paths(request: WorkspaceImportRequest) -> WorkspaceImportResponse:
    return get_chat_service().import_local_paths(request.text)
@app.get("/api/workspace/tree", response_model=WorkspaceTreeResponse)
async def workspace_tree(path: str = "/workspace") -> WorkspaceTreeResponse:
    return get_chat_service().list_workspace(path)
@app.post("/api/workspace/upload", response_model=WorkspaceUploadResponse, status_code=201)
async def upload_workspace_file(
    request: Request,
    filename: str = Query(min_length=1),
    target_dir: str = "/workspace/data/uploads",
) -> WorkspaceUploadResponse:
    return await get_chat_service().upload_workspace_file(
        filename=filename,
        content_type=request.headers.get("content-type"),
        target_dir=target_dir,
        chunks=request.stream(),
    )
@app.get("/api/workspace/preview", response_model=WorkspaceFilePreview)
async def workspace_file_preview(
    path: str,
    max_bytes: int = Query(200_000, ge=1024, le=1_000_000),
) -> WorkspaceFilePreview:
    return get_chat_service().preview_workspace_file(path, max_bytes=max_bytes)
@app.get("/api/workspace/raw")
async def workspace_file_raw(path: str) -> FileResponse:
    target = get_chat_service().resolve_workspace_file(path)
    return FileResponse(target, filename=target.name)
@app.delete("/api/workspace/file", response_model=WorkspaceDeleteResponse)
async def delete_workspace_file(path: str) -> WorkspaceDeleteResponse:
    return await get_chat_service().delete_workspace_file(path)
@app.get("/api/workspace/trash", response_model=WorkspaceTrashResponse)
async def workspace_trash() -> WorkspaceTrashResponse:
    return await get_chat_service().list_workspace_trash()
@app.post("/api/workspace/trash/{operation_id}/restore", response_model=WorkspaceRestoreResponse)
async def restore_workspace_trash_item(operation_id: str) -> WorkspaceRestoreResponse:
    return await get_chat_service().restore_workspace_trash_item(operation_id)
@app.post("/api/chat/stream")
async def chat_stream(request: ChatRequest) -> StreamingResponse:
    service = get_chat_service()
    return await _stream_ndjson(
        service.stream_chat(request),
        conversation_id=request.conversation_id,
    )
@app.post("/api/conversations/{conversation_id}/messages/{message_id}/activate-version", response_model=list[MessageRecord])
async def activate_message_version(
    conversation_id: str,
    message_id: str,
    request: ActivateMessageVersionRequest,
) -> list[MessageRecord]:
    return get_chat_service().activate_message_version(
        conversation_id,
        message_id=message_id,
        version_number=request.version_number,
    )
from pydantic import BaseModel, SecretStr
from core.providers import save_key, thinking_capability
class ModelCredentialRequest(BaseModel):
    api_key: SecretStr
@app.put('/api/providers/{provider_id}/credential')
async def set_model_credential(provider_id: str, payload: ModelCredentialRequest, request: Request):
    origin = request.headers.get('origin')
    if not _is_loopback_host(request.client.host if request.client else None) or (origin and origin not in config.WEB_ORIGINS):
        raise HTTPException(403, '密钥仅允许本机 Web 设置')
    spec = next((s for s in get_runtime().models.specs if s['provider_id'] == provider_id and s['kind'] == 'cloud'), None)
    if not spec:
        raise HTTPException(404, '模型供应商未配置')
    key = payload.api_key.get_secret_value().strip()
    if not key or len(key) > 1024 or any(c.isspace() for c in key):
        raise HTTPException(422, '请输入有效密钥')
    save_key(spec['api_key_env'], key)
    return {'status': 'configured'}
class ModelSettingsRequest(BaseModel):
    model_id: str
    workspace_path: str
    thinking_enabled: bool | None = None
    thinking_budget: int | None = None
    workspace_cloud_allowed: bool | None = None
@app.patch('/api/model-settings')
async def set_model_settings(payload: ModelSettingsRequest, request: Request):
    origin = request.headers.get('origin')
    if not _is_loopback_host(request.client.host if request.client else None) or (origin and origin not in config.WEB_ORIGINS):
        raise HTTPException(403, '设置仅允许本机 Web 修改')
    if payload.workspace_path != str(config.WORKSPACE_PATH.resolve()):
        raise HTTPException(409, '工作区已变化，请刷新页面后重新确认')
    service = get_chat_service()
    if service._active_conversations:
        raise HTTPException(409, '请等待当前回答完成或停止后再修改设置')
    spec = next((s for s in get_runtime().models.specs if s['id'] == payload.model_id), None)
    if spec is None:
        raise HTTPException(404, '模型未配置')
    fields = payload.model_fields_set
    capability = thinking_capability(spec)
    if 'thinking_enabled' in fields and capability is None:
        raise HTTPException(422, '当前模型未提供可配置的思考开关，按供应商默认行为运行')
    if 'thinking_budget' in fields:
        if capability != 'switch_budget':
            raise HTTPException(422, '当前模型不支持思考强度设置')
        if payload.thinking_budget is not None and not 128 <= payload.thinking_budget <= 131072:
            raise HTTPException(422, '思考强度需为 128~131072 的整数 token 数')
    if payload.workspace_cloud_allowed is not None and spec['kind'] != 'cloud':
        raise HTTPException(422, '本地模型无需云端授权')
    from core.model_settings import update_settings
    changes = {}
    if 'thinking_enabled' in fields:
        changes['thinking'] = payload.thinking_enabled  # None 表示恢复默认
    if 'thinking_budget' in fields:
        changes['budget'] = payload.thinking_budget
    if payload.workspace_cloud_allowed is not None:
        changes['workspace'] = payload.workspace_cloud_allowed
    update_settings(spec['id'], **changes)
    return {'status': 'saved'}
@app.get('/api/package-jobs')
async def package_job_list():
    router = get_runtime().router
    try:
        response = await router._client.get(router._backend_urls['skill-runner'] + '/package-jobs', timeout=10)
        response.raise_for_status()
        return response.json()
    except Exception:
        raise HTTPException(503, '无法获取安装状态，请检查工具服务；这不代表安装已结束')
@app.post('/api/package-jobs/{job_id}/cancel')
async def package_job_cancel(job_id: str, request: Request):
    origin = request.headers.get('origin')
    if not _is_loopback_host(request.client.host if request.client else None) or (origin and origin not in config.WEB_ORIGINS):
        raise HTTPException(403, '只允许本机页面停止安装')
    return await get_runtime().router.dispatch('package_cancel', {'job_id':job_id})
async def _stop_tool_containers() -> None:
    """Best-effort stop of all project tool containers; never blocks the exit."""
    import logging
    log = logging.getLogger(__name__)
    try:
        proc = await asyncio.create_subprocess_exec(
            'docker', 'compose', '--profile', 'websearch', 'stop',
            cwd=str(config.PROJECT_ROOT),
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            _, stderr = await asyncio.wait_for(proc.communicate(), timeout=90)
        except asyncio.TimeoutError:
            proc.kill()
            log.warning('docker compose stop timed out; continuing shutdown')
            return
        if proc.returncode:
            log.warning('docker compose stop exited %s: %s', proc.returncode, (stderr or b'')[:300])
    except FileNotFoundError:
        pass  # Docker CLI unavailable — nothing to stop.
    except Exception:
        log.exception('Failed to stop tool containers; continuing shutdown')
@app.post('/api/heartbeat')
async def heartbeat(request: Request) -> dict:
    if not _is_loopback_host(request.client.host if request.client else None):
        raise HTTPException(403, '仅允许本机页面心跳')
    app.state.auto_exit.observe_heartbeat(time.monotonic())
    return {'ok': True, 'auto_exit': app.state.auto_exit.enabled}
async def _auto_exit_watcher() -> None:
    import logging
    log = logging.getLogger(__name__)
    while True:
        await asyncio.sleep(3)
        state = app.state.auto_exit
        if not state.enabled:
            continue
        try:
            from core.stock_service import stock_service
            stock = await stock_service.status()
        except Exception:
            stock = {'online': False}
        try:
            if not state.should_exit(
                time.monotonic(),
                active_conversations=len(get_chat_service()._active_conversations),
                stock_online=bool(stock.get('online')),
                stock_busy=bool((stock.get('worker') or {}).get('busy')),
            ):
                continue
            log.info('Page closed: no heartbeat for %.0fs; shutting the stack down', state.timeout_seconds)
            await _request_stack_shutdown()
            return
        except Exception:
            log.exception('Auto-exit attempt failed; will retry')
# -- Static web hosting (production build) ---------------------------------
# The BFF serves the built SPA itself, so the browser sees a single origin.
# `npm run dev` inside apps/web stays available for development on 5173.
_WEB_DIST = config.PROJECT_ROOT / 'apps' / 'web' / 'dist'
if _WEB_DIST.is_dir():
    from fastapi.staticfiles import StaticFiles
    app.mount('/', StaticFiles(directory=_WEB_DIST, html=True), name='web')
