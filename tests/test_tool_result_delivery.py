"""Actual router contracts, with HTTP and workspace files kept in temporary roots."""
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import httpx

from core import config
from core.tool_registry import ToolRegistry
from core.tool_router import ToolRouter
from core.research_session import ResearchSession
from tests.test_file_paging import FileOps, PathGuard


class ToolDeliveryTests(unittest.IsolatedAsyncioTestCase):
    async def test_spilled_results_can_be_read_in_each_workspace_without_crossing_roots(self):
        with tempfile.TemporaryDirectory() as temporary, patch.object(config, 'TOOL_RESULT_MAX_CHARS', 40):
            registries = ToolRegistry(config.TOOLS_DIR, enable_websearch=True)
            for workspace in ('default', 'named'):
                root = Path(temporary)/workspace
                root.mkdir()
                files = FileOps(PathGuard(str(root)))
                requests = []
                def handle(request):
                    args = json.loads(request.content)
                    requests.append((request.url.path, args))
                    path = args.get('path', '').removeprefix('/workspace/')
                    if request.url.path.endswith('file_write'):
                        result = files.write(str(root/path), args['content'])
                    elif request.url.path.endswith('file_read'):
                        result = files.read(str(root/path), offset=args.get('offset',0), max_chars=args.get('max_chars'))
                    else:
                        result = {'text':workspace*60}
                    return httpx.Response(200,json=result)
                router = ToolRouter('http://files','http://runner','http://search', Mock(), Mock(), registries, enable_websearch=True)
                await router._client.aclose()
                router._client = httpx.AsyncClient(transport=httpx.MockTransport(handle))
                self.addAsyncCleanup(router._client.aclose)
                result = await router.dispatch('web_search', {'query':'test'})
                write = next(args for route,args in requests if route.endswith('file_write'))
                self.assertTrue(write['path'].startswith('/workspace/.tool-results/'))
                self.assertIn(write['path'],result['text'])
                restored, offset = '', 0
                while True:
                    page = await router.dispatch('file_read', {'path':write['path'],'offset':offset})
                    span = page['range']
                    restored += page['content'][:span['end']-span['start']]
                    if page['next_offset'] is None:break
                    offset = page['next_offset']
                self.assertEqual(restored,workspace*60)
                self.assertNotIn('仅显示',page['content'])

    async def test_readonly_cloud_does_not_create_spill_files(self):
        registry = ToolRegistry(config.TOOLS_DIR, enable_websearch=True)
        router = ToolRouter('http://files','http://runner','http://search',Mock(),Mock(),registry)
        self.addAsyncCleanup(router._client.aclose)
        router.cloud = True
        router.workspace_snapshot = SimpleNamespace(record={'cloud_write':False})
        with patch.object(config,'TOOL_RESULT_MAX_CHARS',10):
            result = {'text':'x'*100}
            self.assertEqual(await router._limit_result('web_search',result,'test'),result)

    def test_research_keeps_editing_but_excludes_background_side_effects(self):
        registry = ToolRegistry(config.TOOLS_DIR, enable_websearch=True, stock_bridge_url='http://unused')
        run = ResearchSession(SimpleNamespace(known_tools=registry.known_tools),conversation_id='s',model='test',allowed=True)
        run.methods.add('read-method')
        for name in ('file_edit','file_convert','package_list','stock_report_read'):
            self.assertIn(name,run.tool_names)
        for name in ('git_commit','stock_research_submit','stock_research_resume','stock_research_cancel','stock_theme_research_submit','stock_track_follow','stock_track_ignore'):
            self.assertNotIn(name,run.tool_names)

    async def test_stream_and_dispatch_share_research_control_boundary(self):
        registry = ToolRegistry(config.TOOLS_DIR)
        router = ToolRouter('http://unused','http://unused','http://unused',Mock(),Mock(),registry)
        self.addAsyncCleanup(router._client.aclose)
        router.research = SimpleNamespace(active=True)
        with self.assertRaises(PermissionError):
            await router.dispatch('skill_run',{'skill_name':'unused'})
        with self.assertRaises(PermissionError):
            _ = [e async for e in router.dispatch_stream('skill_run',{'skill_name':'unused'})]
