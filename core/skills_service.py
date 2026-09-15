"""Runtime skill switches managed from the Web page.

Currently covers web search (SearXNG + skill-websearch containers). The toggle
persists to data/private/skill-switches.json, starts/stops the Docker
profile, and flips the tool registry so the model only sees tools that are
actually usable. The env flag ENABLE_WEBSEARCH only seeds the initial value.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from pathlib import Path

import httpx

from . import config
from .tool_registry import ToolRegistry

logger = logging.getLogger(__name__)


class SkillSwitches:
    def __init__(self) -> None:
        self.settings_path = Path(os.environ.get(
            'SKILL_SWITCHES',
            str(config._PROJECT_ROOT / 'data' / 'private' / 'skill-switches.json')))
        self.lock = asyncio.Lock()
        # The env flag seeds the default until the user toggles from the page.
        self.websearch_enabled = self._read().get('websearch', config.ENABLE_WEBSEARCH)

    def _read(self) -> dict:
        try:
            if self.settings_path.exists():
                value = json.loads(self.settings_path.read_text(encoding='utf-8'))
                if isinstance(value, dict) and isinstance(value.get('websearch'), bool):
                    return value
        except (OSError, ValueError):
            pass
        return {}

    def save(self) -> None:
        self.settings_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.settings_path.with_suffix('.tmp')
        tmp.write_text(json.dumps({'websearch': self.websearch_enabled},
                                  ensure_ascii=False, indent=2), encoding='utf-8')
        tmp.replace(self.settings_path)

    async def _compose(self, *args: str, timeout: float = 150.0) -> None:
        proc = await asyncio.create_subprocess_exec(
            'docker', 'compose', '--profile', 'websearch', *args,
            cwd=str(config._PROJECT_ROOT),
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        if proc.returncode:
            detail = (stderr or b'').decode('utf-8', 'replace').strip()[:400]
            raise RuntimeError(detail or f'docker compose {" ".join(args)} 退出码 {proc.returncode}')

    async def _websearch_healthy(self) -> bool:
        try:
            async with httpx.AsyncClient(timeout=3, trust_env=False) as client:
                response = await client.get(config.SKILL_WEBSEARCH_URL.rstrip('/') + '/health')
            return response.status_code == 200
        except httpx.HTTPError:
            return False

    async def _wait_websearch(self, timeout: float = 120.0) -> None:
        deadline = asyncio.get_running_loop().time() + timeout
        last_error: Exception | None = None
        while asyncio.get_running_loop().time() < deadline:
            try:
                async with httpx.AsyncClient(timeout=5, trust_env=False) as client:
                    response = await client.get(config.SKILL_WEBSEARCH_URL.rstrip('/') + '/health')
                if response.status_code == 200:
                    return
            except httpx.HTTPError as exc:
                last_error = exc
            await asyncio.sleep(2)
        raise RuntimeError('联网搜索服务未在限定时间内就绪，请检查 Docker 容器状态' + (
            f'：{last_error}' if last_error else ''))

    def apply_to(self, runtime, enabled: bool) -> None:
        """Flip the live registry and router so web tools appear/vanish for the model."""
        bridge = getattr(runtime.router, '_stock_bridge', None)
        bridge_url = getattr(bridge, '_base_url', '') if bridge else ''
        registry = ToolRegistry(config.TOOLS_DIR, enable_websearch=enabled,
                                stock_bridge_url=bridge_url)
        runtime.tool_registry._tools = registry._tools
        if enabled:
            runtime.router._backend_urls['skill-websearch'] = config.SKILL_WEBSEARCH_URL.rstrip('/')
        else:
            runtime.router._backend_urls.pop('skill-websearch', None)

    async def set_websearch(self, runtime, enabled: bool, active_check=None) -> dict:
        async with self.lock:
            if active_check and active_check():
                raise ValueError('聊天正在执行，请等待本轮结束后再切换技能')
            already = ('web_search' in runtime.tool_registry.known_tools)
            if enabled == self.websearch_enabled and already == enabled:
                return self.status(runtime)
            if enabled:
                await self._compose('up', '-d')
                await self._wait_websearch()
                self.apply_to(runtime, True)
            else:
                self.apply_to(runtime, False)
                await self._compose('stop')
            self.websearch_enabled = enabled
            self.save()
            return self.status(runtime)

    def status(self, runtime) -> dict:
        return {'websearch': {
            'enabled': self.websearch_enabled,
            'active': 'web_search' in runtime.tool_registry.known_tools,
            'url': config.SKILL_WEBSEARCH_URL,
        }}

    async def restore(self, runtime) -> None:
        if not self.websearch_enabled:
            return
        try:
            if await self._websearch_healthy():
                # Containers survived (or were already started); only the tool
                # registry needs to catch up with the saved switch.
                self.apply_to(runtime, True)
                return
            # Do not go through set_websearch here: its no-op shortcut only
            # looks at the registry, which the env flag may already have
            # seeded, and would skip actually starting the containers.
            async with self.lock:
                await self._compose('up', '-d')
                await self._wait_websearch()
                self.apply_to(runtime, True)
                self.save()
        except Exception:
            logger.exception('Web search skill restore failed; toggle it from the Web page')


skill_switches = SkillSwitches()
