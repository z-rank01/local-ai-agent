"""Local service lifecycle, separate from model-visible stock tools."""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
from pathlib import Path

import httpx

from . import config
from .stock_bridge import StockBridge
from .tool_registry import ToolRegistry

# Pid of the stock backend that scripts/start-daily.ps1 started, so the chat backend
# can tell "the launcher owns it" from "nobody owns it".
LAUNCHER_PID_ENV = 'STOCK_LAUNCHER_PID'


def launcher_owns_backend(port: int) -> bool:
    """True when this process was started by the launcher rather than by the page.

    scripts/start-daily.ps1 exports the backend's pid into the chat backend's
    environment; the page-driven path does not, so a missing or dead pid means the
    backend would have to be spawned here -- which cannot write the stock repository.
    """
    raw = os.environ.get(LAUNCHER_PID_ENV, '').strip()
    if not raw.isdigit():
        return False
    try:
        os.kill(int(raw), 0)   # signal 0 only checks that the process exists
    except (OSError, ValueError):
        return False
    return True


class StockService:
    def __init__(self):
        self.settings_path = Path(os.environ.get('DAILY_SERVICES_SETTINGS', str(config._PROJECT_ROOT / 'data/private/daily-services.json')))
        self.lock = asyncio.Lock()
        # No child handle: the backend is started by scripts/start-daily.ps1, never
        # from here (see launcher_owns_backend).
        self.settings = self.read_settings()

    def read_settings(self):
        self.configuration_error = ''
        value = {}
        try:
            if self.settings_path.exists():
                value = json.loads(self.settings_path.read_text(encoding='utf-8'))
            if not isinstance(value, dict):
                raise ValueError('invalid settings')
            for key in ('root', 'state_dir'):
                if key in value and not isinstance(value[key], str):
                    raise ValueError('invalid path')
            for key in ('enabled', 'worker'):
                if key in value and type(value[key]) is not bool:
                    raise ValueError('invalid switch')
            if 'port' in value and (type(value['port']) is not int or not 1024 <= value['port'] <= 65535):
                raise ValueError('invalid port')
        except (OSError, ValueError):
            value = {}
            self.configuration_error = '服务配置不可读或无效，请在连接配置中重新保存；聊天可继续使用'
        return {'root': value.get('root', os.environ.get('STOCK_PROJECT_ROOT', '')),
                'state_dir': value.get('state_dir', os.environ.get('STOCK_STATE_DIR', 'simulation')),
                'port': value.get('port', int(os.environ.get('STOCK_SERVICE_PORT', '8765'))),
                'enabled': value.get('enabled', False),
                # Task execution defaults to ON when the skill is enabled;
                # users pause it explicitly from the Web panel.
                'worker': value.get('worker', True)}

    def save(self):
        self.settings_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.settings_path.with_suffix('.tmp')
        tmp.write_text(json.dumps(self.settings, ensure_ascii=False, indent=2), encoding='utf-8')
        tmp.replace(self.settings_path)

    def paths(self):
        if not self.settings['root']:
            raise ValueError('请先填写股票仓库路径并保存')
        root = Path(self.settings['root']).resolve()
        state = (root / self.settings['state_dir']).resolve()
        if not state.is_relative_to(root):
            raise ValueError('状态目录必须位于所选股票仓库内')
        if not (root / 'scripts/run_paper.py').is_file():
            raise ValueError('所选目录不是股票仓库，缺少 scripts/run_paper.py')
        return root, state

    @property
    def url(self):
        return f"http://127.0.0.1:{self.settings['port']}"

    async def request(self, path, body=None):
        _, state = self.paths()
        token = (state / '.paper-control-token').read_text(encoding='utf-8').strip()
        async with httpx.AsyncClient(timeout=8, trust_env=False) as client:
            response = await client.request('GET' if body is None else 'POST', self.url + path,
                                            headers={'Authorization': 'Bearer ' + token}, json=body)
        if response.is_error:
            try:
                message = response.json().get('error') or response.json().get('detail')
            except ValueError:
                message = None
            raise ValueError(message or f'股票控制接口返回 HTTP {response.status_code}')
        result = response.json()
        if path.endswith('/status'):
            expected = hashlib.sha256((state / 'state.sqlite').as_posix().casefold().encode()).hexdigest()[:16]
            if result.get('workspace_id') != expected:
                raise ValueError('端口上的股票服务属于另一个状态目录，请先核对配置')
        return result

    async def status(self):
        result = {'settings': self.settings, 'online': False, 'url': self.url}
        if self.configuration_error:
            return {**result, 'message': self.configuration_error}
        try:
            result.update(await self.request('/api/control/status'))
            result['online'] = True
        except (ValueError, OSError, httpx.HTTPError) as exc:
            result['message'] = str(exc) if isinstance(exc, ValueError) else '股票后台未运行或控制接口尚未就绪'
        return result

    async def attach(self, runtime, enabled):
        if runtime.stock_bridge:
            await runtime.stock_bridge.close()
        bridge = None
        if enabled:
            _, state = self.paths()
            bridge = StockBridge(self.url, (state / '.paper-master-token').read_text(encoding='utf-8').strip())
        runtime.stock_bridge = runtime.router._stock_bridge = bridge
        # Keep registry identity: existing agents and prompt builders share it.
        registry = ToolRegistry(config.TOOLS_DIR, enable_websearch=config.ENABLE_WEBSEARCH,
                                stock_bridge_url=self.url if enabled else '')
        runtime.tool_registry._tools = registry._tools

    async def start(self, runtime):
        root, state = self.paths()
        status = await self.status()
        if not status['online']:
            # Never start over a live listener, including an old backend without this API.
            try:
                reader, writer = await asyncio.wait_for(asyncio.open_connection('127.0.0.1', self.settings['port']), 1)
            except (OSError, asyncio.TimeoutError):
                pass
            else:
                writer.close()
                await writer.wait_closed()
                raise ValueError('端口已被占用；请关闭旧股票服务或更换端口，不会终止未知进程')
            # The backend must not be spawned from here.  Measured 2026-09-28: a
            # process created by this backend cannot write the stock repository
            # (PermissionError 13 on simulation/, then SQLite "unable to open
            # database file"), and the restriction follows the whole process tree --
            # cmd, a batch hop and powershell Start-Process all failed the same way.
            # scripts/start-daily.ps1 starts it as a top-level process instead, which
            # works.  Say so plainly rather than failing with a buried traceback.
            if not launcher_owns_backend(self.settings['port']):
                raise ValueError(
                    '股票后台需要由启动器启动（当前版本不再由聊天后端派生，'
                    '因为那样启动的进程无法写入股票仓库）。'
                    '请关闭页面与聊天后端，然后重新双击 启动.bat。')
            raise ValueError('股票后台未就绪，请稍后刷新状态；不会重复启动。')
        if self.settings['worker']:
            await self.request('/api/control/action', {'action': 'worker_start'})
        await self.attach(runtime, True)
        self.settings['enabled'] = True
        self.save()

    async def action(self, runtime, body, active_check=None):
        async with self.lock:
            if active_check and active_check():
                raise ValueError('聊天正在执行，请等待本轮结束后再切换服务')
            action = body.get('action')
            if action == 'configure':
                if (await self.status())['online']:
                    raise ValueError('修改配置前请先关闭股票后台')
                port = body.get('port', 8765)
                if type(port) is not int or not 1024 <= port <= 65535 or port in (9510, 5173):
                    raise ValueError('端口须为 1024～65535，且不能占用 Web/BFF 端口')
                old = self.settings.copy()
                if not isinstance(body.get('root'), str) or not isinstance(body.get('state_dir', 'simulation'), str):
                    raise ValueError('仓库路径与状态目录必须是文本')
                self.settings.update(root=body.get('root', ''), state_dir=body.get('state_dir', 'simulation'), port=port)
                try:
                    self.paths()
                except Exception:
                    self.settings = old
                    raise
                self.save()
                self.configuration_error = ''
            elif action == 'start':
                await self.start(runtime)
            elif action == 'stop':
                try:
                    await self.request('/api/control/status')  # Verify identity before mutation.
                except httpx.ConnectError:
                    pass  # Already stopped: repeated close is safe.
                else:
                    await self.request('/api/control/action', {'action': 'shutdown'})
                    # Shutdown is acknowledged before the backend actually exits.
                    # Do not report success while its listener is still alive.
                    try:
                        await asyncio.wait_for(self.wait_until_stopped(), timeout=10)
                    except asyncio.TimeoutError:
                        raise ValueError('股票后台尚未退出，请稍后刷新状态') from None
                await self.attach(runtime, False)
                self.settings['enabled'] = False
                self.save()
                return {'settings': self.settings.copy(), 'online': False, 'url': self.url,
                        'message': '股票后台已关闭，聊天可继续使用'}
            elif action in ('worker_start', 'worker_pause', 'recover', 'prepare_resume', 'confirm_resume', 'operator', 'market_model_configure'):
                if action == 'market_model_configure':
                    model = body.get('model')
                    from .market_research import available_models
                    catalog = await available_models(runtime)
                    if not isinstance(model, dict) or set(model) != {'provider', 'model'} or not any(
                            item['provider'] == model['provider'] and item['model'] == model['model'] and
                            item['status'] == 'configured' for item in catalog):
                        raise ValueError('所选简报分析模型未安装或未配置密钥')
                await self.request('/api/control/status')
                result = await self.request('/api/control/action', body)
                if action in ('worker_start', 'worker_pause', 'recover'):
                    self.settings['worker'] = action != 'worker_pause'
                    self.save()
                if action in ('prepare_resume', 'operator'):
                    return result
            else:
                raise ValueError('未知服务操作')
            return await self.status()

    async def wait_until_stopped(self):
        while True:
            try:
                _, writer = await asyncio.wait_for(
                    asyncio.open_connection('127.0.0.1', self.settings['port']), timeout=3)
            except ConnectionRefusedError:
                return
            except asyncio.TimeoutError:
                pass  # An unresponsive listener is not proof of shutdown.
            else:
                writer.close()
                await writer.wait_closed()
            await asyncio.sleep(0.1)


stock_service = StockService()
