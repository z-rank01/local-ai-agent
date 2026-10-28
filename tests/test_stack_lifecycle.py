import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from fastapi import HTTPException


class StackLifecycleTests(unittest.IsolatedAsyncioTestCase):
    async def test_exit_drains_before_stock_containers_and_process_exit(self):
        from bff import app as module
        from plugins.finance.service import stock_service
        order=[]
        async def runs(): order.append('runs')
        async def stock(*args,**kwargs): order.append('stock'); return {'status':'shutting_down'}
        async def containers(): order.append('containers')
        async def plugins(): order.append('plugins')
        with patch.object(module,'get_chat_service',return_value=SimpleNamespace(runs=SimpleNamespace(close=runs))), \
             patch.object(module,'get_runtime',return_value=SimpleNamespace(container_packages={'example':SimpleNamespace(client=SimpleNamespace(stop=plugins))})), \
             patch.object(stock_service,'status',AsyncMock(return_value={'online':True})), \
             patch.object(stock_service,'request',side_effect=stock), \
             patch.object(module,'_stop_tool_containers',side_effect=containers), \
             patch.object(module,'_stack_shutdown_targets',return_value={'bff':[1]}), \
             patch.object(module,'schedule_stack_shutdown',side_effect=lambda t:order.append('exit')):
            await module._request_stack_shutdown()
        self.assertEqual(order,['runs','plugins','stock','containers','exit'])

    async def test_unconfirmed_cleanup_preserves_service_and_error(self):
        from bff import app as module
        with patch.object(module,'get_chat_service',return_value=SimpleNamespace(runs=SimpleNamespace(
                close=AsyncMock(side_effect=RuntimeError('进程尚未停止'))))), \
             patch.object(module,'_stop_tool_containers',AsyncMock()) as containers, \
             patch.object(module,'schedule_stack_shutdown') as exit:
            with self.assertRaises(HTTPException) as error:
                await module._request_stack_shutdown()
            self.assertEqual(error.exception.status_code,409)
            containers.assert_not_awaited(); exit.assert_not_called()
            self.assertEqual(module.app.state.shutdown_error,'进程尚未停止')

    async def test_disconnecting_exit_observer_does_not_cancel_shutdown(self):
        from bff import app as module
        entered=asyncio.Event(); release=asyncio.Event()
        async def drain(): entered.set(); await release.wait(); return {'done':True}
        with patch.object(module,'_drain_stack',side_effect=drain) as execute:
            task=asyncio.create_task(module._request_stack_shutdown())
            await entered.wait(); task.cancel()
            with self.assertRaises(asyncio.CancelledError): await task
            self.assertFalse(module.app.state.shutdown_task.cancelled())
            other=asyncio.create_task(module._request_stack_shutdown()); release.set()
            self.assertEqual(await other,{'done':True}); self.assertEqual(execute.call_count,1)

    async def test_script_heartbeat_does_not_arm_page_liveness(self):
        from bff import app as module
        from core.auto_exit import AutoExitState
        module.app.state.auto_exit=AutoExitState()
        await module.heartbeat(SimpleNamespace(client=SimpleNamespace(host='127.0.0.1')))
        self.assertFalse(module.app.state.auto_exit.armed)

    def test_build_targets_registered_workspace_groups(self):
        from pathlib import Path
        source = (Path(__file__).resolve().parents[1] / 'scripts/start-daily.ps1').read_text(encoding='utf-8')
        self.assertIn("Get-Content -LiteralPath (Join-Path $dailyRoot 'data/private/workspace-containers.json') -Raw -Encoding UTF8", source)
        self.assertIn('ws-$($_.id)-files', source)
        self.assertIn('ws-$($_.id)-runner', source)
        self.assertIn('build $dailyWorkspaceServices', source)
        self.assertIn('$dailyExistingContainerSet.Contains("ws-$dailyWorkspaceId-files")', source)
        self.assertIn("Invoke-DailyDockerOperation $dailyWorkspaceContext 'workspace_sync' $dailyWorkspaceId", source)
        self.assertIn("'--force-recreate'", (Path(__file__).resolve().parents[1] / 'scripts/service-supervisor.ps1').read_text(encoding='utf-8'))


class ProcessIdentityTests(unittest.TestCase):
    def test_recorded_process_ids_determine_host_memory_without_port_guesses(self):
        import json
        import tempfile
        from pathlib import Path
        from types import SimpleNamespace
        from scripts.measure_harness_performance import process_memory
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            metadata = root / 'data/private/runtime-processes.json'
            metadata.parent.mkdir(parents=True)
            metadata.write_text(json.dumps({'bff_pid': 11, 'stock_pid': 22,
                'stock_server_pid': 22, 'launcher_pid': 44}), encoding='utf-8')
            rows = [{'Id': 11, 'WorkingSet64': 1000}, {'Id': 22, 'WorkingSet64': 2000},
                    {'Id': 44, 'WorkingSet64': 4000}]
            with patch('scripts.measure_harness_performance.ROOT', root), \
                    patch('scripts.measure_harness_performance.subprocess.run',
                          return_value=SimpleNamespace(returncode=0, stdout=json.dumps(rows), stderr='')):
                result = process_memory(0, 17665)
            self.assertEqual(result['stock'], 2000)
            self.assertEqual(result['total_rss_bytes'], 7000)
            self.assertEqual({row['role'] for row in result['processes']}, {'bff', 'stock', 'launcher'})
            metadata.write_text(json.dumps({'bff_pid': 11, 'launcher_pid': 44}), encoding='utf-8')
            with patch('scripts.measure_harness_performance.ROOT', root):
                with self.assertRaisesRegex(RuntimeError, 'stock_pid'):
                    process_memory(0, 17665)

