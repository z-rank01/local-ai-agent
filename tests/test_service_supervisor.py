"""Launcher service controller client: loopback protocol and failure messages.

No network and no Docker: the HTTP client is replaced, so these cover only how the
chat backend reacts to what the launcher-side controller returns.
"""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

import httpx

from core import service_supervisor


def response(status: int, payload) -> httpx.Response:
    text = payload if isinstance(payload, str) else json.dumps(payload)
    return httpx.Response(status, text=text, request=httpx.Request('POST', service_supervisor.SUPERVISOR_URL))


class SupervisorClientTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.token_path = Path(self.tmp.name) / 'service-supervisor.token'
        patcher = patch.object(service_supervisor, 'TOKEN_PATH', self.token_path)
        patcher.start()
        self.addCleanup(patcher.stop)

    def _client(self, responder):
        """Stand-in for httpx.AsyncClient whose post() runs `responder`."""
        post = AsyncMock(side_effect=responder)
        client = AsyncMock()
        client.post = post
        client.__aenter__ = AsyncMock(return_value=client)
        client.__aexit__ = AsyncMock(return_value=False)
        return client, post

    async def _run(self, responder, operation='stock_start'):
        client, post = self._client(responder)
        with patch.object(service_supervisor.httpx, 'AsyncClient', return_value=client):
            result = await service_supervisor.run_operation(operation)
        return result, post

    # -- failure paths: these are what the page and the run guide quote ----------

    async def test_missing_token_file_points_at_the_launcher(self):
        self.assertFalse(self.token_path.exists())
        with self.assertRaises(RuntimeError) as caught:
            await service_supervisor.run_operation('stock_start')
        self.assertIn('本机服务控制器未启动', str(caught.exception))
        self.assertIn('启动.bat', str(caught.exception))

    async def test_blank_token_file_is_rejected_without_a_request(self):
        self.token_path.write_text('   \n', encoding='utf-8')
        client, post = self._client(lambda *a, **k: response(200, {'ok': True}))
        with patch.object(service_supervisor.httpx, 'AsyncClient', return_value=client):
            with self.assertRaises(RuntimeError) as caught:
                await service_supervisor.run_operation('stock_start')
        self.assertIn('凭据为空', str(caught.exception))
        post.assert_not_awaited()

    async def test_transport_error_is_reported_as_unavailable(self):
        self.token_path.write_text('x' * 64, encoding='utf-8')
        with self.assertRaises(RuntimeError) as caught:
            await self._run(httpx.ConnectError('connection refused'))
        self.assertIn('本机服务控制器不可用', str(caught.exception))

    async def test_launcher_detail_is_surfaced(self):
        self.token_path.write_text('x' * 64, encoding='utf-8')
        with self.assertRaises(RuntimeError) as caught:
            await self._run(lambda *a, **k: response(503, {'detail': '股票仓库路径不存在'}))
        self.assertIn('股票仓库路径不存在', str(caught.exception))

    async def test_error_without_json_detail_falls_back_to_status_code(self):
        self.token_path.write_text('x' * 64, encoding='utf-8')
        with self.assertRaises(RuntimeError) as caught:
            await self._run(lambda *a, **k: response(503, '<html>gateway</html>'))
        self.assertIn('HTTP 503', str(caught.exception))

    async def test_invalid_json_body_is_rejected(self):
        self.token_path.write_text('x' * 64, encoding='utf-8')
        with self.assertRaises(RuntimeError) as caught:
            await self._run(lambda *a, **k: response(200, 'not json'))
        self.assertIn('无效结果', str(caught.exception))

    async def test_ok_false_is_rejected_even_with_http_200(self):
        self.token_path.write_text('x' * 64, encoding='utf-8')
        with self.assertRaises(RuntimeError) as caught:
            await self._run(lambda *a, **k: response(200, {'ok': False}))
        self.assertIn('无效结果', str(caught.exception))

    # -- success path -----------------------------------------------------------

    async def test_success_returns_payload_and_sends_the_token(self):
        self.token_path.write_text('t' * 64, encoding='utf-8')
        result, post = await self._run(
            lambda *a, **k: response(200, {'ok': True, 'operation': 'stock_start', 'output': 'started', 'pid': 4321}))
        self.assertEqual(result['pid'], 4321)
        self.assertEqual(post.await_args.kwargs['headers']['Authorization'], 'Bearer ' + 't' * 64)
        self.assertEqual(post.await_args.kwargs['json'], {'operation': 'stock_start'})
        self.assertEqual(post.await_args.args[0], service_supervisor.SUPERVISOR_URL + '/api/operation')

    async def test_client_ignores_proxy_environment(self):
        # trust_env=False keeps a stray HTTP_PROXY from hijacking a loopback call.
        self.token_path.write_text('t' * 64, encoding='utf-8')
        captured = {}
        real_client = httpx.AsyncClient

        def factory(*args, **kwargs):
            captured.update(kwargs)
            return real_client(*args, **kwargs)

        with patch.object(service_supervisor.httpx, 'AsyncClient', side_effect=factory):
            with self.assertRaises(RuntimeError):
                await service_supervisor.run_operation('stock_start', timeout=0.2)
        self.assertIs(captured.get('trust_env'), False)


if __name__ == '__main__':
    unittest.main()
