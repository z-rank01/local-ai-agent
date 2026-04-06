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

        # ── Truncate large string values in results (A5) ────────────────
        max_chars = self._registry.get_max_result_chars(tool)
        if max_chars and isinstance(result, dict):
            for key in ("content", "stdout", "stderr", "output", "text"):
                val = result.get(key)
                if isinstance(val, str) and len(val) > max_chars:
                    result = dict(result)  # shallow copy to avoid mutating cache
                    result[key] = (
                        val[:max_chars]
                        + f"\n...[已截断，仅显示前 {max_chars} 字符，共 {len(val)} 字符]"
                    )

        # Auto-chain: when file_read returns unsupported binary, try file_convert
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

    async def _dispatch_convert(self, path: str, session_id: str) -> dict:
        """Internal helper to call file_convert on skill-runner."""
        backend = self._registry.get_backend("file_convert")
        base_url = self._backend_urls[backend]
        resp = await self._client.post(
            f"{base_url}/tool/file_convert",
            json={"path": path},
        )
        if resp.status_code >= 400:
            return {"error": f"file_convert HTTP {resp.status_code}"}
        return resp.json()

    async def close(self) -> None:
        await self._client.aclose()
