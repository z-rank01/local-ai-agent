"""FastAPI app exposing a stable frontend-facing protocol."""
from __future__ import annotations
import asyncio
import logging
import os
import signal
import subprocess
import time
import httpx
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager, aclosing
from urllib.parse import quote
from fastapi import FastAPI, HTTPException, Query, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse, JSONResponse
from core import config, model_settings
from core.workspaces import tool_token
from core.auto_exit import AutoExitState
from .deps import get_chat_service, get_runtime, shutdown_runtime
from .schemas import (
    WorkspaceChange, WorkspaceDirectoryRequest, WorkspaceRenameRequest,
    ActivateMessageVersionRequest,
    AppStatus,
    ChatRequest,
    ConversationSummary,
    CreateConversationRequest,
    BriefDeliveryRequest,
    EditMessageRequest,
    MessageRecord,
    ModelInfo,
    ProviderInfo,
    RegenerateRequest,
    ResearchMethodChoice,
    ReportPageRequest,
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

# uvicorn configures only its own loggers, so application loggers such as
# core.agent had no handler and their INFO lines (prefetch, compaction) were
# dropped -- a silent failure was invisible in the daily log.  Configure the root
# logger once, leaving uvicorn's own loggers untouched.
logging.basicConfig(
    level=os.environ.get('LOG_LEVEL', 'INFO').upper(),
    format='%(asctime)s %(levelname)s %(name)s %(message)s',
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
from .capabilities import router as capabilities_router
app.include_router(capabilities_router)
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
@app.exception_handler(PermissionError)
async def filesystem_permission_error(request: Request, exc: PermissionError):
    return JSONResponse(status_code=403, content={'detail': '当前进程无法访问所选文件，请检查目录权限或工作区工具容器。'})

@app.exception_handler(FileNotFoundError)
async def filesystem_missing_error(request: Request, exc: FileNotFoundError):
    return JSONResponse(status_code=404, content={'detail': '文件或目录不存在，请刷新工作区。'})

@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}
@app.get("/api/status", response_model=AppStatus)
async def status(workspace_id: str = "default") -> AppStatus:
    return get_chat_service().for_workspace(workspace_id).app_status()
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
@app.get('/api/admin/stock-models')
async def stock_analysis_models(request: Request, refresh: int = 0):
    if not _is_loopback_host(request.client.host if request.client else None):
        raise HTTPException(403, '仅允许本机查看分析模型')
    from core.market_research import available_models
    return await available_models(get_runtime(), refresh=bool(refresh))
@app.post('/api/internal/market-event-analysis', include_in_schema=False)
async def market_event_analysis(request: Request):
    from core.market_research import authorize, generate
    authorize(request)
    body = await request.json()
    fields = {'provider', 'model', 'prompt', 'material','execution_profile'}
    if (not isinstance(body, dict) or set(body) not in (fields, fields | {'generation'}) or
            ('generation' in body and not isinstance(body['generation'], dict))):
        raise HTTPException(422, '分析请求字段无效')
    return await generate(get_runtime(), body['provider'], body['model'], body['prompt'], body['material'],
                          generation=body.get('generation'),execution_profile=body['execution_profile'])
@app.post('/api/internal/stock-public-search', include_in_schema=False)
async def stock_public_search(request: Request):
    from core.market_research import authorize, public_search
    authorize(request)
    return await public_search(get_runtime(), await request.json())

@app.post('/api/internal/research-generation', include_in_schema=False)
async def research_generation(request: Request):
    from core.market_research import authorize
    from core.research_generation import generate
    authorize(request)
    return await generate(get_runtime(), await request.json())

@app.post('/api/internal/research-generation/stop', include_in_schema=False)
async def stop_research_generation(request: Request):
    from core.market_research import authorize
    from core.research_generation import stop_job
    authorize(request)
    body = await request.json()
    if not isinstance(body, dict) or set(body) != {'job_id'} or not isinstance(body['job_id'], str):
        raise HTTPException(422, '需要指定内部任务标识')
    return await stop_job(body['job_id'])
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
        if body.get('action') in ('operator','worker_start','recover'):
            cid=body.get('conversation_id')
            if not cid: raise ValueError('请先选择任务所属会话')
            service=get_chat_service()
            conversation=service._require_conversation(cid)
            spec,_=service._execution_model(conversation)
            workspace=service.workspaces.get(conversation.workspace_id)
            from core.model_execution import freeze_profile
            body['execution_profile']=freeze_profile(spec,cid,conversation.workspace_id,workspace['cloud_read'])
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
        completed = subprocess.run(['netstat', '-ano', '-p', 'tcp'], capture_output=True,
                                   text=True, errors='replace', timeout=10)
    except Exception:
        return []
    pids = set()
    for line in (completed.stdout or '').splitlines():
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
    task = getattr(app.state, 'shutdown_task', None)
    if task is None or task.done():
        task = asyncio.create_task(_drain_stack(), name='stack-shutdown')
        app.state.shutdown_task = task
    # Losing the requesting page must not cancel the actual shutdown operation.
    return await asyncio.shield(task)

async def _drain_stack() -> dict:
    app.state.shutdown_error = ''
    try:
        return await _finish_stack_shutdown()
    except (ValueError, RuntimeError, httpx.HTTPError, HTTPException) as exc:
        message = str(exc.detail) if isinstance(exc, HTTPException) else str(exc)
        app.state.shutdown_error = message
        state = getattr(app.state, 'auto_exit', None)
        if state:
            state.exit_error = message
        raise HTTPException(409, message) from exc

async def _finish_stack_shutdown() -> dict:
    """Confirm run cleanup, stock checkpoint persistence and containers before scheduling our exit."""
    await get_chat_service().runs.close()
    targets = _stack_shutdown_targets()
    from core.stock_service import stock_service
    service_status = await stock_service.status()
    if service_status['online']:
        try:
            await stock_service.request('/api/control/action', {'action': 'shutdown'}, timeout=40)
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc
    elif targets.get('stock-bridge'):
        raise HTTPException(409, '股票后台未接入安全关闭接口，请先在股票服务中暂停并关闭后再退出')
    # The backend shuts itself down after draining; never kill an arbitrary port owner.
    targets.pop('stock-bridge', None)
    await _stop_tool_containers()
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
    workspace_id: str | None = None,
) -> list[ConversationSummary]:
    return get_chat_service().list_conversations(limit=limit, offset=offset, query=query, workspace_id=workspace_id)

@app.get('/api/research-methods')
async def research_methods():
    return {'methods': await get_chat_service().research_methods()}
@app.post("/api/conversations", response_model=ConversationSummary, status_code=201)
async def create_conversation(request: CreateConversationRequest) -> ConversationSummary:
    return get_chat_service().create_conversation(request.title, request.model, request.workspace_id)
@app.get("/api/conversations/{conversation_id}", response_model=ConversationSummary)
async def get_conversation(conversation_id: str) -> ConversationSummary:
    return get_chat_service().get_conversation(conversation_id)
@app.patch("/api/conversations/{conversation_id}", response_model=ConversationSummary)
async def update_conversation(
    conversation_id: str,
    request: UpdateConversationRequest,
) -> ConversationSummary:
    return get_chat_service().update_conversation(conversation_id, title=request.title, model=request.model)

@app.put('/api/conversations/{conversation_id}/research-method', response_model=ConversationSummary)
async def update_research_method(conversation_id: str, request: ResearchMethodChoice) -> ConversationSummary:
    return await get_chat_service().update_research_method(conversation_id, request.method_id)
@app.delete("/api/conversations/{conversation_id}", status_code=204)
async def delete_conversation(conversation_id: str) -> Response:
    get_chat_service().delete_conversation(conversation_id)
    return Response(status_code=204)
@app.get("/api/conversations/{conversation_id}/messages", response_model=list[MessageRecord])
async def list_messages(conversation_id: str) -> list[MessageRecord]:
    return get_chat_service().get_messages(conversation_id)


@app.post('/api/conversations/{conversation_id}/report-page')
async def read_report_page(conversation_id: str, request: ReportPageRequest):
    return await get_chat_service().read_report_page(
        conversation_id, request.message_id, request.page, request.request_id,
        section=request.section, revision=request.revision)


@app.post('/api/conversations/{conversation_id}/brief-delivery', response_model=MessageRecord)
async def deliver_brief(conversation_id: str, payload: BriefDeliveryRequest, request: Request):
    require_local_control(request)
    return get_chat_service().deliver_workspace_brief(conversation_id, payload.path)

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
@app.middleware('http')
async def browser_session(request: Request, call_next):
    # Stock callbacks retain their existing dedicated credential checks.
    protected = request.url.path.startswith('/api/') and not request.url.path.startswith('/api/internal/')
    if os.environ.get('DAILY_SERVICES') == '1' and protected and request.method != 'OPTIONS':
        from core.ui_session import authenticated, session_token
        if not authenticated(request):
            return JSONResponse(status_code=403, content={'detail': '请通过启动.bat重新打开页面'})
        response = await call_next(request)
        if request.headers.get('X-Harness-Session'):
            response.set_cookie('harness_session', session_token(), httponly=True, samesite='strict')
        return response
    return await call_next(request)

def workspace_service(workspace_id: str):
    return get_chat_service().for_workspace(workspace_id)

@app.get('/api/workspaces')
async def list_workspaces():
    manager = get_chat_service().workspaces
    return [manager.public(r) for r in manager.records()]

@app.post('/api/workspaces', status_code=201)
async def create_workspace(request: Request, payload: WorkspaceChange):
    require_local_control(request)
    return await get_chat_service().workspaces.update(payload.model_dump(exclude_unset=True))

@app.patch('/api/workspaces/{workspace_id}')
async def update_workspace(workspace_id: str, request: Request, payload: WorkspaceChange):
    require_local_control(request)
    return await get_chat_service().workspaces.update(payload.model_dump(exclude_unset=True), workspace_id)

@app.post('/api/workspaces/{workspace_id}/activate')
async def activate_workspace(workspace_id: str, request: Request):
    require_local_control(request)
    manager = get_chat_service().workspaces
    await manager.ensure(workspace_id, apply_pending=True)
    return manager.public(manager.get(workspace_id))

@app.delete('/api/workspaces/{workspace_id}', status_code=204)
async def delete_workspace(workspace_id: str, request: Request):
    require_local_control(request)
    manager = get_chat_service().workspaces
    async with manager.lock:
        if manager.active:
            raise HTTPException(409, '请等待任务结束后删除工作区')
        old = manager.get(workspace_id)
        manager.delete(workspace_id)
        try:
            await manager.reconcile()
        except (RuntimeError, httpx.HTTPError) as exc:
            manager.save(old)
            manager.manifest()
            raise HTTPException(503, "容器清理未完成，工作区关联已保留，请恢复 Docker 后重试。") from exc
    return Response(status_code=204)

@app.post("/api/workspace/import-local-paths", response_model=WorkspaceImportResponse)
async def import_local_paths(request: WorkspaceImportRequest, http_request: Request, workspace_id: str = 'default'):
    require_local_control(http_request)
    service = workspace_service(workspace_id)
    service.assert_workspace_idle()
    return service.import_local_paths(request.text)

@app.get("/api/workspace/tree", response_model=WorkspaceTreeResponse)
async def workspace_tree(path: str = "/workspace", workspace_id: str = 'default'):
    return workspace_service(workspace_id).list_workspace(path)

@app.post("/api/workspace/upload", response_model=WorkspaceUploadResponse, status_code=201)
async def upload_workspace_file(request: Request, filename: str, target_dir: str | None = None, workspace_id: str = 'default'):
    require_local_control(request)
    service = workspace_service(workspace_id)
    if workspace_id != 'default' or service._snapshot.record['roots']:
        await service.workspaces.ensure(workspace_id)
    lease = service.workspaces.acquire(service._snapshot, '__files__')
    try:
        return await service.upload_workspace_file(filename=filename, content_type=request.headers.get('content-type'),
            target_dir=target_dir or service._snapshot.cwd, chunks=request.stream())
    finally:
        service.workspaces.active.pop(lease, None)

@app.get("/api/workspace/preview", response_model=WorkspaceFilePreview)
async def workspace_file_preview(path: str, max_bytes: int = 200000, workspace_id: str = 'default'):
    return workspace_service(workspace_id).preview_workspace_file(path, max_bytes=max_bytes)

@app.get("/api/workspace/raw")
async def workspace_file_raw(path: str, workspace_id: str = 'default'):
    target = workspace_service(workspace_id).resolve_workspace_file(path)
    return FileResponse(target, filename=target.name)

@app.delete("/api/workspace/file", response_model=WorkspaceDeleteResponse)
async def delete_workspace_file(request: Request, path: str, workspace_id: str = 'default'):
    require_local_control(request)
    service = workspace_service(workspace_id)
    await service.workspaces.ensure(workspace_id)
    lease = service.workspaces.acquire(service._snapshot, '__files__')
    try:
        return await service.delete_workspace_file(path)
    finally:
        service.workspaces.active.pop(lease, None)

@app.get("/api/workspace/trash", response_model=WorkspaceTrashResponse)
async def workspace_trash(workspace_id: str = 'default'):
    service = workspace_service(workspace_id)
    await service.workspaces.ensure(workspace_id)
    return await service.list_workspace_trash()

@app.post("/api/workspace/trash/{operation_id}/restore", response_model=WorkspaceRestoreResponse)
async def restore_workspace_trash_item(operation_id: str, request: Request, workspace_id: str = 'default'):
    require_local_control(request)
    service = workspace_service(workspace_id)
    await service.workspaces.ensure(workspace_id)
    lease = service.workspaces.acquire(service._snapshot, '__files__')
    try:
        return await service.restore_workspace_trash_item(operation_id)
    finally:
        service.workspaces.active.pop(lease, None)

@app.post('/api/workspace/directory')
async def create_workspace_directory(request: Request, payload: WorkspaceDirectoryRequest, workspace_id: str = 'default'):
    require_local_control(request)
    service = workspace_service(workspace_id)
    if workspace_id != 'default' or service._snapshot.record['roots']:
        await service.workspaces.ensure(workspace_id)
    lease = service.workspaces.acquire(service._snapshot, '__files__')
    try:
        target = service._snapshot.resolve(payload.path)
        try:
            target.mkdir(parents=False, exist_ok=False)
        except PermissionError:
            await service._call_skill_files("POST", "/workspace/directory", json_body={"path": payload.path})
        except FileExistsError:
            raise HTTPException(409, '目录已存在')
        return {'path': service._snapshot.virtual(target)}
    finally:
        service.workspaces.active.pop(lease, None)

@app.post('/api/workspace/rename')
async def rename_workspace_file(request: Request, payload: WorkspaceRenameRequest, workspace_id: str = 'default'):
    require_local_control(request)
    service = workspace_service(workspace_id)
    if workspace_id != 'default' or service._snapshot.record['roots']:
        await service.workspaces.ensure(workspace_id)
    lease = service.workspaces.acquire(service._snapshot, '__files__')
    try:
        src, dst = service._snapshot.resolve(payload.src), service._snapshot.resolve(payload.dst)
        if src in service._snapshot.host_roots or dst.exists():
            raise HTTPException(409, '不能重命名根目录或覆盖已有文件')
        if src.parent != dst.parent:
            raise HTTPException(422, '重命名只能修改名称')
        try:
            src.rename(dst)
        except PermissionError:
            await service._call_skill_files("POST", "/tool/file_rename", json_body={"src": payload.src, "dst": payload.dst})
        return {'path': service._snapshot.virtual(dst)}
    finally:
        service.workspaces.active.pop(lease, None)

@app.post("/api/chat/stream")
async def chat_stream(request: ChatRequest) -> StreamingResponse:
    service = get_chat_service()
    return await _stream_ndjson(
        service.stream_chat(request),
        conversation_id=request.conversation_id,
    )

@app.get('/api/runs')
async def list_runs(workspace_id: str | None = None):
    service = get_chat_service()
    if workspace_id:
        service.workspaces.get(workspace_id)
    from core.stock_service import stock_service
    stock = await stock_service.status()
    rows = [service.run_summary(row) for row in service.runs.store.list(workspace_id)]
    if stock['online']:
        try:
            if not workspace_id or workspace_id == 'stock':
                rows.extend(await stock_service.request('/api/control/runs'))
        except (ValueError, httpx.HTTPError, OSError) as exc:
            stock['message'] = str(exc) if isinstance(exc, ValueError) else '股票任务列表暂时不可读'
    return {'runs': rows, 'stock_service': {k:stock.get(k) for k in ('online','worker','message')},
            'shutdown_error':getattr(app.state,'shutdown_error',''),
            'page_connections':len(getattr(getattr(app.state,'auto_exit',None),'pages',()))}

@app.get('/api/runs/{run_id}')
async def get_run(run_id: str):
    if run_id.startswith('stock:'):
        from core.stock_service import stock_service
        if not (await stock_service.status())['online']:
            raise HTTPException(409, '股票服务离线，无法确认任务当前状态')
        rows = await stock_service.request('/api/control/runs')
        row = next((r for r in rows if r['id'] == run_id), None)
        if row is None:
            raise HTTPException(404, '股票任务不存在')
        return row
    return get_chat_service().run_summary(get_chat_service().runs.store.get(run_id))

@app.get('/api/runs/{run_id}/events')
async def run_events(run_id: str, after_seq: int = Query(0, ge=0)):
    if run_id.startswith('stock:'):
        # Stock owns its status and checkpoint. Observe snapshots; don't copy its scheduler into C.
        async def stock_events():
            last = None
            while True:
                row = await get_run(run_id)
                if row != last:
                    yield UIStreamEvent(event='run.updated', run_id=run_id, data={'run': row})
                    last = row
                if row['status'] in ('SUCCEEDED','FAILED','STOPPED','INTERRUPTED','PAUSED','QUEUED'):
                    return
                await asyncio.sleep(1)
        return await _stream_ndjson(stock_events(), conversation_id=None)
    service = get_chat_service()
    service.runs.public(service.runs.store.get(run_id))
    async def events():
        async for event in service.runs.subscribe(run_id, after_seq):
            yield UIStreamEvent.model_validate(event)
    return await _stream_ndjson(events(), conversation_id=None)

@app.post('/api/runs/{run_id}/control')
async def control_run(run_id: str, request: Request):
    require_local_control(request)
    body = await request.json()
    if not isinstance(body, dict) or set(body) != {'action'}:
        raise HTTPException(422, '操作须为pause、resume或stop')
    if body['action'] not in ('pause', 'resume', 'stop'):
        raise HTTPException(422, '未知运行操作')
    if run_id.startswith('stock:'):
        from core.stock_service import stock_service
        if not (await stock_service.status())['online']:
            raise HTTPException(409, '股票服务离线；请通过唯一入口开启后操作')
        try:
            return await stock_service.request('/api/control/jobs/'+quote(run_id[6:],safe=''), body, timeout=40)
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc
    return await get_chat_service().control_run(run_id, body['action'])
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
    workspace_id: str = "default"
    thinking_enabled: bool | None = None
    thinking_budget: int | None = None
    workspace_cloud_allowed: bool | None = None
    workspace_cloud_write_allowed: bool | None = None
@app.patch('/api/model-settings')
async def set_model_settings(payload: ModelSettingsRequest, request: Request):
    origin = request.headers.get('origin')
    if not _is_loopback_host(request.client.host if request.client else None) or (origin and origin not in config.WEB_ORIGINS):
        raise HTTPException(403, '设置仅允许本机 Web 修改')
    manager = getattr(get_chat_service(), "workspaces", None)
    record = manager.get(payload.workspace_id) if manager else {"cloud_read": model_settings.workspace_allowed(), "cloud_write": False}
    managed = manager.snapshot(payload.workspace_id).managed if manager else config.WORKSPACE_PATH.resolve()
    if payload.workspace_path != str(managed):
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
    if payload.workspace_cloud_write_allowed is not None:
        if spec['kind'] != 'cloud':
            raise HTTPException(422, '本地模型无需云端授权')
        if payload.workspace_cloud_write_allowed:
            from core.model_settings import workspace_allowed
            if not (payload.workspace_cloud_allowed if payload.workspace_cloud_allowed is not None
                    else record["cloud_read"]):
                raise HTTPException(422, '请先允许云端读取工作区，再开启写入与执行')
    from core.model_settings import update_settings
    changes = {}
    if 'thinking_enabled' in fields:
        changes['thinking'] = payload.thinking_enabled  # None 表示恢复默认
    if 'thinking_budget' in fields:
        changes['budget'] = payload.thinking_budget
    if payload.workspace_cloud_allowed is not None:
        changes['workspace'] = payload.workspace_cloud_allowed
    if payload.workspace_cloud_write_allowed is not None:
        changes['cloud_write'] = payload.workspace_cloud_write_allowed
    if payload.workspace_id != 'default':
        changes.pop('workspace', None)
        changes.pop('cloud_write', None)
    update_settings(spec['id'], **changes)
    if payload.workspace_cloud_allowed is not None:
        record['cloud_read'] = payload.workspace_cloud_allowed
        if not record['cloud_read']: record['cloud_write'] = False
    if payload.workspace_cloud_write_allowed is not None:
        record['cloud_write'] = payload.workspace_cloud_write_allowed
    if record.get('pending_config'):
        record['pending_config'].update(cloud_read=record['cloud_read'], cloud_write=record['cloud_write'])
    if manager: manager.save(record)
    return {'status': 'saved'}
@app.get('/api/package-jobs')
async def package_job_list(workspace_id: str = "default"):
    router = get_runtime().router
    try:
        manager = get_chat_service().workspaces
        record = manager.get(workspace_id)
        await manager.verify(record)
        response = await router._client.get(f"http://127.0.0.1:{record['runner_port']}/package-jobs", timeout=10, headers={"X-Workspace-Token": tool_token(workspace_id)})
        response.raise_for_status()
        return response.json()
    except Exception:
        raise HTTPException(503, '无法获取安装状态，请检查工具服务；这不代表安装已结束')
@app.post('/api/package-jobs/{job_id}/cancel')
async def package_job_cancel(job_id: str, request: Request, workspace_id: str = "default"):
    origin = request.headers.get('origin')
    if not _is_loopback_host(request.client.host if request.client else None) or (origin and origin not in config.WEB_ORIGINS):
        raise HTTPException(403, '只允许本机页面停止安装')
    manager = get_chat_service().workspaces
    record = manager.get(workspace_id)
    try:
        await manager.verify(record)
    except (RuntimeError, httpx.HTTPError) as exc:
        raise HTTPException(503, '安装任务所属的工作区工具容器不可用') from exc
    response = await get_runtime().router._client.post(f"http://127.0.0.1:{record['runner_port']}/tool/package_cancel", json={'job_id': job_id}, headers={'X-Workspace-Token': tool_token(workspace_id)})
    response.raise_for_status()
    return response.json()
async def _stop_tool_containers() -> None:
    """Only the unique launcher executes Docker; failure leaves BFF alive for diagnosis."""
    from core.service_supervisor import run_operation
    await run_operation('stack_shutdown', timeout=100)
@app.post('/api/heartbeat')
async def heartbeat(request: Request) -> dict:
    if not _is_loopback_host(request.client.host if request.client else None):
        raise HTTPException(403, '仅允许本机页面心跳')
    # Compatibility probe only. A script ping or run observer is not an open page.
    return {'ok': True, 'auto_exit': app.state.auto_exit.enabled}

@app.get('/api/keepalive')
async def keepalive(request: Request):
    """Server-sent liveness stream: the page holds this connection open.
    A background/minimised tab does not throttle it, unlike timers; when
    every connection is gone the auto-exit grace period starts."""
    if not _is_loopback_host(request.client.host if request.client else None):
        raise HTTPException(403, '仅允许本机页面保持连接')
    state = app.state.auto_exit

    async def event_stream():
        identity = object()
        state.page_opened(identity, time.monotonic())
        try:
            while True:
                if await request.is_disconnected():
                    break
                yield ': ping\n\n'
                await asyncio.sleep(5)
        finally:
            state.page_closed(identity, time.monotonic())

    return StreamingResponse(event_stream(), media_type='text/event-stream',
                               headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'})
async def _auto_exit_watcher() -> None:
    import logging
    log = logging.getLogger(__name__)
    while True:
        await asyncio.sleep(3)
        state = app.state.auto_exit
        if not state.enabled:
            continue
        try:
            if not state.should_exit(time.monotonic()):
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
