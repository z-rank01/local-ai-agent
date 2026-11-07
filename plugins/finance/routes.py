"""Finance HTTP compatibility routes, mounted by the plugin loader."""
import httpx
from fastapi import APIRouter, Request, HTTPException
from bff.deps import get_chat_service, get_runtime
from bff.app import require_local_control, _is_loopback_host

router = APIRouter()
@router.get('/api/admin/stock-service')
async def stock_service_status(request: Request):
    if not _is_loopback_host(request.client.host if request.client else None):
        raise HTTPException(403, '仅允许本机查看服务控制')
    from plugins.finance.service import stock_service
    return await stock_service.status()
@router.get('/api/admin/stock-models')
async def stock_analysis_models(request: Request, refresh: int = 0):
    if not _is_loopback_host(request.client.host if request.client else None):
        raise HTTPException(403, '仅允许本机查看分析模型')
    from plugins.finance.market import available_models
    return await available_models(get_runtime(), refresh=bool(refresh))
@router.post('/api/internal/market-event-analysis', include_in_schema=False)
async def market_event_analysis(request: Request):
    from plugins.finance.market import authorize, generate
    authorize(request)
    body = await request.json()
    fields = {'provider', 'model', 'prompt', 'material','execution_profile'}
    if (not isinstance(body, dict) or set(body) not in (fields, fields | {'generation'}) or
            ('generation' in body and not isinstance(body['generation'], dict))):
        raise HTTPException(422, '分析请求字段无效')
    return await generate(get_runtime(), body['provider'], body['model'], body['prompt'], body['material'],
                          generation=body.get('generation'),execution_profile=body['execution_profile'])
@router.post('/api/internal/stock-public-search', include_in_schema=False)
async def stock_public_search(request: Request):
    from plugins.finance.market import authorize, public_search
    authorize(request)
    return await public_search(get_runtime(), await request.json())

@router.post('/api/internal/research-generation', include_in_schema=False)
async def research_generation(request: Request):
    from plugins.finance.market import authorize
    from plugins.finance.generation import generate
    authorize(request)
    return await generate(get_runtime(), await request.json())

@router.post('/api/internal/research-generation/stop', include_in_schema=False)
async def stop_research_generation(request: Request):
    from plugins.finance.market import authorize
    from plugins.finance.generation import stop_job
    authorize(request)
    body = await request.json()
    if not isinstance(body, dict) or set(body) != {'job_id'} or not isinstance(body['job_id'], str):
        raise HTTPException(422, '需要指定内部任务标识')
    return await stop_job(body['job_id'])
@router.post('/api/admin/stock-service')
async def stock_service_action(request: Request):
    require_local_control(request)
    if get_chat_service()._active_conversations:
        raise HTTPException(409, '聊天正在执行，请等待本轮结束后再切换服务')
    from plugins.finance.service import stock_service
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
    except httpx.HTTPError as exc:
        raise HTTPException(503, '股票服务连接失败，请刷新状态') from exc
