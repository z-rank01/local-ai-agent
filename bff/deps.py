"""Dependency accessors for the frontend adapter API."""

from __future__ import annotations

from core.runtime import RuntimeServices, build_runtime

from .service import ChatSessionService

_runtime: RuntimeServices | None = None
_chat_service: ChatSessionService | None = None


def get_runtime() -> RuntimeServices:
    global _runtime
    if _runtime is None:
        _runtime = build_runtime()
    return _runtime


def get_chat_service() -> ChatSessionService:
    global _chat_service
    if _chat_service is None:
        _chat_service = ChatSessionService(get_runtime())
    return _chat_service


async def shutdown_runtime() -> None:
    global _runtime, _chat_service
    if _runtime is not None:
        await _runtime.close()
    _runtime = None
    _chat_service = None