import logging
from typing import Any

import httpx

from audit_logger import AuditLogger
from policy_engine import PolicyEngine
from tool_registry import ToolRegistry

logger = logging.getLogger("gateway.router")


class ToolRouter:
    def __init__(
        self,
        skill_files_url: str,
        skill_runner_url: str,
        skill_websearch_url: str,
        policy: PolicyEngine,
        audit: AuditLogger,
        registry: ToolRegistry,
        *,
        enable_websearch: bool = False,
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
        self._client = httpx.AsyncClient(timeout=300.0)

    async def dispatch(self, tool: str, params: dict[str, Any], session_id: str = "default") -> Any:
        if tool not in self._registry.known_tools:
            raise ValueError(f"Unknown tool: {tool!r}")

        self._policy.check(tool, params)

        backend = self._registry.get_backend(tool)
        base_url = self._backend_urls[backend]

        resp = await self._client.post(
            f"{base_url}/tool/{tool}",
            json=params,
        )

        if resp.status_code == 403:
            raise PermissionError(resp.json().get("detail", "Forbidden"))
        if resp.status_code == 404:
            raise FileNotFoundError(resp.json().get("detail", "Not found"))
        if resp.status_code >= 400:
            # Extract detail from FastAPI error responses (422 validation, 500 internal)
            try:
                body = resp.json()
                detail = body.get("detail", str(body))
            except Exception:
                detail = resp.text[:500] if resp.text else f"HTTP {resp.status_code}"
            raise RuntimeError(
                f"Tool {tool!r} returned HTTP {resp.status_code}: {detail}"
            )

        result = resp.json()
        self._audit.record(tool, {"session_id": session_id, "params": params, "status": "ok"})
        return result

    async def close(self) -> None:
        await self._client.aclose()
