"""
Tool router — dispatches tool calls to skill microservices via HTTP.
"""

import json
import hashlib
import os
import asyncio
import time
import uuid
from contextlib import suppress, aclosing
import logging
from typing import Any

import httpx

from .audit_logger import AuditLogger
from .conversation_store import ConversationStore
from .policy_engine import PolicyEngine
from .tool_registry import ToolRegistry

logger = logging.getLogger("core.router")


class ToolRouter:
    def __init__(
        self,
        skill_files_url: str,
        skill_runner_url: str,
        skill_websearch_url: str,
        policy: PolicyEngine,
        audit: AuditLogger,
        registry: ToolRegistry,
        store: ConversationStore | None = None,
        *,
        enable_websearch: bool = False,
        stock_bridge=None,
    ):
        self._backend_urls = {
            "skill-files": skill_files_url.rstrip("/"),
            "skill-runner": skill_runner_url.rstrip("/"),
        }
        if enable_websearch:
            self._backend_urls["skill-websearch"] = skill_websearch_url.rstrip("/")
        self._policy = policy
        self._audit = audit
        self._registry = registry
        self._store = store
        self._stock_bridge = stock_bridge
        self._client = httpx.AsyncClient(timeout=300.0, trust_env=False)
        if os.environ.get("DAILY_SERVICES") == "1":
            from .workspaces import tool_token
            self.workspace_token = tool_token("default")

    def set_backend(self, backend: str, url: str | None) -> None:
        """Update a transport binding without exposing the routing table."""
        if url is None:
            self._backend_urls.pop(backend, None)
        else:
            self._backend_urls[backend] = url.rstrip('/')

    def _workspace_params(self, tool, params):
        snapshot = getattr(self, 'workspace_snapshot', None)
        if snapshot is None or self._registry.get_backend(tool) not in {'skill-files', 'skill-runner'}:
            return params
        if tool in {'shell_exec', 'code_exec', 'skill_run'}:
            snapshot.validate_links()
        params = dict(params)
        for key in ('path', 'src', 'dst', 'directory', 'cwd'):
            if isinstance(params.get(key), str):
                params[key] = snapshot.virtual(snapshot.resolve(params[key]))
        return params

    def _headers(self, backend):
        token = getattr(self, 'workspace_token', '')
        return {'X-Workspace-Token': token} if token and backend in {'skill-files', 'skill-runner'} else {}

    @property
    def known_tools(self) -> frozenset[str]:
        """Names registered at startup; research derives its surface from this."""
        return self._registry.known_tools

    def _check_research_tool(self, tool):
        from .research_session import RESEARCH_BLOCKED_TOOLS
        research = getattr(self, 'research', None)
        if research is not None and research.active and tool in RESEARCH_BLOCKED_TOOLS:
            raise PermissionError(f'当前聊天研究不开放此工具：{tool}；取证、计算、资料编辑与发布保持可用')

    async def authorize_publication(self, params):
        """The agent's separate review must cross the same publication boundary."""
        self._policy.check('brief_publish', params)
        if hasattr(self, 'workspace_validate'):
            await self.workspace_validate()

    async def _limit_result(self, tool, result, session_id):
        cap = self._registry.get_max_result_chars(tool)
        # Page boundaries, hashes and body belong to the file/fetch service.
        if not cap or not isinstance(result, dict) or 'range' in result or 'next_offset' in result:
            return result
        snapshot = getattr(self, 'workspace_snapshot', None)
        if getattr(self, 'cloud', False) and snapshot and not snapshot.record['cloud_write']:
            return result  # Read-only grants cannot create a spill file.
        result = dict(result)
        for key in ('content', 'stdout', 'stderr', 'output', 'text'):
            value = result.get(key)
            if not isinstance(value, str) or len(value) <= cap:
                continue
            digest = hashlib.sha256(value.encode('utf-8')).hexdigest()
            path = f'/workspace/.tool-results/{tool}-{digest}.txt'
            saved = await self.dispatch('file_write', {'path':path, 'content':value}, session_id)
            if not isinstance(saved, dict) or not saved.get('written'):
                raise RuntimeError('完整工具结果未能保存；不能仅交付截断内容')
            result[key] = value[:cap] + f'\n[仅显示前{cap}字符，共{len(value)}字符；完整结果可用file_read读取：{path}]'
        return result

    async def dispatch(self, tool: str, params: dict[str, Any], session_id: str = "default") -> Any:
        self._check_research_tool(tool)
        if tool not in self._registry.known_tools:
            raise ValueError(f"Unknown tool: {tool!r}")

        if tool in {'shell_exec', 'code_exec', 'skill_run'}:
            async with aclosing(self.dispatch_stream(tool, params, session_id)) as events:
                async for event in events:
                    if event.get('event') == 'result':
                        self._audit.record(tool,{'session_id':session_id,'params':params,'status':'ok'})
                        return event['result']
            raise RuntimeError('执行工具未返回最终结果')

        if tool in {"shell_exec", "code_exec"}:
            params = {"cwd": getattr(self, "default_cwd", "/workspace"), **params}
        params = self._workspace_params(tool, params)
        self._policy.check(tool, params)
        cap = self._registry.get_max_result_chars(tool)
        if cap and tool in ('file_read', 'web_fetch'):
            params = {**params, 'max_chars':min(params.get('max_chars') or cap, cap)}

        backend = self._registry.get_backend(tool)
        if backend == 'research-runtime':
            research = getattr(self, 'research', None)
            if research is None:
                raise RuntimeError('研究工具需要当前会话和工作区上下文')
            return await (research.begin(params) if tool == 'research_begin' else research.publish(params))
        if backend == "local-runtime":
            result = self._dispatch_local(tool, params)
            self._audit.record(tool, {"session_id": session_id, "params": params, "status": "ok"})
            return result
        if backend == "stock-bridge":
            if self._stock_bridge is None:
                raise RuntimeError("股票能力未配置（缺少 STOCK_BRIDGE_URL）。")
            result = await self._stock_bridge.call_tool(tool, params, session_id)
            self._audit.record(tool, {"session_id": session_id, "params": params, "status": "ok"})
            return result
        if hasattr(self, "workspace_validate"):
            await self.workspace_validate()
        base_url = self._backend_urls[backend]

        if tool == 'web_fetch':
            params = {**params, 'include_file': bool(getattr(self, 'research', None) and self.research.active)}
        resp = await self._client.post(
            f"{base_url}/tool/{tool}",
            json=params, headers=self._headers(backend),
        )

        if resp.status_code == 403:
            raise PermissionError(resp.json().get("detail", "Forbidden"))
        if resp.status_code == 404:
            raise FileNotFoundError(resp.json().get("detail", "Not found"))
        if resp.status_code >= 400:
            try:
                body = resp.json()
                detail = body.get("detail", str(body))
            except Exception:
                detail = resp.text[:500] if resp.text else f"HTTP {resp.status_code}"
            raise RuntimeError(
                f"Tool {tool!r} returned HTTP {resp.status_code}: {detail}"
            )

        result = resp.json()
        if tool == 'web_fetch' and isinstance(result, dict):
            research = getattr(self, 'research', None)
            if research and research.active:
                result = await research.save_source(result)
            else:
                result.pop('_source_file', None)

        result = await self._limit_result(tool, result, session_id)

        # Auto-chain: file_read → file_convert for unsupported binary files
        if tool == "file_read" and isinstance(result, dict) and result.get("unsupported"):
            if "file_convert" in self._registry.known_tools:
                convert_path = result.get("path") or params.get("path")
                if convert_path:
                    logger.info("Auto-chaining file_read → file_convert for %s", convert_path)
                    try:
                        convert_result = await self._dispatch_convert(convert_path, session_id)
                        if not convert_result.get("unsupported") and not convert_result.get("error"):
                            self._audit.record(
                                "file_convert_auto",
                                {"session_id": session_id, "path": convert_path, "status": "ok"},
                            )
                            return convert_result
                    except Exception as exc:
                        logger.warning("Auto file_convert failed for %s: %s", convert_path, exc)

        self._audit.record(tool, {"session_id": session_id, "params": params, "status": "ok"})
        return result

    async def dispatch_stream(self, tool, params, session_id='default'):
        self._check_research_tool(tool)
        if tool not in ('shell_exec', 'code_exec', 'skill_run'):
            started = time.monotonic()
            pending = asyncio.create_task(self.dispatch(tool, params, session_id))
            try:
                yield {'event':'heartbeat', 'elapsed':0, 'text':'工具正在执行，等待服务返回结果…'}
                while not pending.done():
                    done, _ = await asyncio.wait({pending}, timeout=2)
                    if not done:
                        yield {'event':'heartbeat', 'elapsed':round(time.monotonic()-started, 1), 'text':'工具仍在执行，等待服务返回结果…'}
                yield {'event':'result', 'result':await pending}
            finally:
                if not pending.done(): pending.cancel()
                with suppress(asyncio.CancelledError): await pending
            return
        if tool not in self._registry.known_tools:
            raise ValueError('Unknown tool')
        if tool in {"shell_exec", "code_exec"}:
            params = {"cwd": getattr(self, "default_cwd", "/workspace"), **params}
        params = self._workspace_params(tool, params)
        self._policy.check(tool, params)
        backend = self._registry.get_backend(tool)
        if hasattr(self, 'workspace_validate'):
            await self.workspace_validate()
        execution_id=uuid.uuid4().hex
        completed=False
        try:
            async with self._client.stream('POST', self._backend_urls[backend] + f'/tool/{tool}/stream', json=params,
                    headers={**self._headers(backend),'X-Execution-ID':execution_id}) as response:
                if response.status_code == 404:
                    raise RuntimeError('工具服务版本过旧，缺少流式执行接口。请沿唯一入口重建工具镜像。')
                response.raise_for_status()
                async for line in response.aiter_lines():
                    if line.strip():
                        event=json.loads(line)
                        completed=completed or event.get('event')=='result'
                        if event.get('event') == 'result':
                            event['result'] = await self._limit_result(tool, event['result'], session_id)
                        yield event
        finally:
            if not completed:
                async def confirm():
                    response=await self._client.post(self._backend_urls[backend]+f'/executions/{execution_id}/stop',
                        headers=self._headers(backend),timeout=30)
                    response.raise_for_status()
                    if response.json().get('confirmed') is not True:
                        raise RuntimeError('工具进程组尚未确认停止；不能确认执行已结束')
                try:
                    await confirm()
                except (httpx.HTTPError, RuntimeError):
                    from .run_manager import current_run
                    control=current_run.get()
                    if control is None or not control.stop_requested:
                        raise
                    await control.wait_for_cleanup(confirm)

    def _dispatch_local(self, tool: str, params: dict[str, Any]) -> Any:
        if self._store is None:
            raise RuntimeError(f"Tool {tool!r} requires conversation storage")
        if getattr(self, 'cloud', False):
            from .agent import model_projection
            def safe_messages(cid):
                conversation = self._store.get_conversation(cid)
                if conversation is None or (hasattr(self, "workspace_id") and conversation.workspace_id != self.workspace_id):
                    raise PermissionError("该会话不属于当前工作区")
                return model_projection(self._store.messages_as_dicts(cid, cloud=True))
            if tool == 'conversation_read':
                cid = str(params.get('conversation_id', ''))
                messages = safe_messages(cid)[-min(30, max(1, int(params.get('max_messages', 12)))):]
                return {'conversation': {'id': cid, 'title': '云端可用会话'}, 'message_count': len(messages), 'messages': messages}
            if tool == 'conversation_search':
                query = str(params.get('query', ''))
                results = []
                for conv in self._store.list_conversations(limit=200, workspace_id=getattr(self, "workspace_id", None)):
                    messages = safe_messages(conv.id)
                    match = next((m for m in messages if query.casefold() in str(m.get('content','')).casefold()), None)
                    if match:
                        results.append({'conversation_id':conv.id, 'title':'云端可用会话', 'snippet':self._excerpt_match(str(match.get('content','')),query)})
                return {'query':query,'results':results[:min(20,int(params.get('limit',5)))], 'count':len(results)}
        if tool == "conversation_search":
            return self._conversation_search(params)
        if tool == "conversation_read":
            return self._conversation_read(params)
        raise ValueError(f"Unknown local-runtime tool: {tool!r}")

    def _conversation_search(self, params: dict[str, Any]) -> dict[str, Any]:
        query = str(params.get("query", "")).strip()
        if not query:
            raise PermissionError("query must be a non-empty string")

        limit = params.get("limit", 5)
        if not isinstance(limit, int):
            try:
                limit = int(limit)
            except Exception as exc:
                raise PermissionError("limit must be an integer") from exc
        limit = max(1, min(limit, 10))

        conversations = self._store.list_conversations(limit=limit, offset=0, query=query, workspace_id=getattr(self, "workspace_id", None))
        results: list[dict[str, Any]] = []
        lowered = query.casefold()
        for conversation in conversations:
            snippet = ""
            match_role = ""
            match_message_id = ""
            for message in self._store.get_messages(conversation.id):
                if lowered in (message.content or "").casefold():
                    snippet = self._excerpt_match(message.content, query)
                    match_role = message.role
                    match_message_id = message.id
                    break
                if lowered in (message.thinking or "").casefold():
                    snippet = self._excerpt_match(message.thinking, query)
                    match_role = message.role
                    match_message_id = message.id
                    break
            results.append({
                "conversation_id": conversation.id,
                "title": conversation.title,
                "model": conversation.model,
                "updated_at": conversation.updated_at,
                "matched_message_id": match_message_id or None,
                "matched_role": match_role or None,
                "snippet": snippet or conversation.title,
            })

        return {"query": query, "count": len(results), "results": results}

    def _conversation_read(self, params: dict[str, Any]) -> dict[str, Any]:
        conversation_id = str(params.get("conversation_id", "")).strip()
        if not conversation_id:
            raise PermissionError("conversation_id must be a non-empty string")

        max_messages = params.get("max_messages", 12)
        if not isinstance(max_messages, int):
            try:
                max_messages = int(max_messages)
            except Exception as exc:
                raise PermissionError("max_messages must be an integer") from exc
        max_messages = max(1, min(max_messages, 30))

        conversation = self._store.get_conversation(conversation_id)
        if conversation is None:
            raise FileNotFoundError("conversation not found")

        if hasattr(self, "workspace_id") and conversation.workspace_id != self.workspace_id:
            raise PermissionError("该会话不属于当前工作区")
        messages = conversation.messages[-max_messages:]
        return {
            "conversation": {
                "id": conversation.id,
                "title": conversation.title,
                "model": conversation.model,
                "created_at": conversation.created_at,
                "updated_at": conversation.updated_at,
            },
            "message_count": len(messages),
            "messages": [
                {
                    "id": message.id,
                    "role": message.role,
                    "created_at": message.created_at,
                    "content": message.content,
                    "thinking": message.thinking,
                    "tool_name": message.tool_name or None,
                    "tool_calls": self._safe_json_loads(message.tool_calls),
                }
                for message in messages
            ],
        }

    @staticmethod
    def _safe_json_loads(raw: str) -> Any:
        if not raw:
            return []
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return raw

    @staticmethod
    def _excerpt_match(text: str, query: str, radius: int = 90) -> str:
        compact = " ".join(text.split())
        lowered = compact.casefold()
        index = lowered.find(query.casefold())
        if index < 0:
            return compact[: radius * 2] + ("..." if len(compact) > radius * 2 else "")
        start = max(0, index - radius)
        end = min(len(compact), index + len(query) + radius)
        prefix = "..." if start > 0 else ""
        suffix = "..." if end < len(compact) else ""
        return f"{prefix}{compact[start:end]}{suffix}"

    async def _dispatch_convert(self, path: str, session_id: str) -> dict:
        path = self._workspace_params("file_convert", {"path": path})["path"]
        backend = self._registry.get_backend("file_convert")
        base_url = self._backend_urls[backend]
        resp = await self._client.post(
            f"{base_url}/tool/file_convert",
            json={"path": path}, headers=self._headers(backend),
        )
        if resp.status_code >= 400:
            return {"error": f"file_convert HTTP {resp.status_code}"}
        return resp.json()

    async def close(self) -> None:
        await self._client.aclose()
