import json
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx
from fastapi import HTTPException
from fastapi.testclient import TestClient

from core.stock_bridge import StockBridge
from core.conversation_store import Message
from bff.service import ChatSessionService
from bff.app import app

REF = 'a' * 64


class ReportPageBridgeTests(unittest.IsolatedAsyncioTestCase):
    async def run_read(self, text, *, code='PUBLIC_OBSERVATION', page=2, status='TOOL_RETURNED'):
        calls=[]
        def handler(request):
            calls.append((request.url.path,json.loads(request.content)))
            if request.url.path.endswith('/prepare'):
                return httpx.Response(200,json={'ticket':'saved-ticket'})
            if request.url.path.endswith('/step'):
                return httpx.Response(200,json={'status':status,'code':code})
            return httpx.Response(200,json={'local_results':[{'sequence':1,'text':text}]})
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            bridge=StockBridge('http://stock','',client=client)
            bridge.set_turn('conversation','active-model-turn','原来的研究问题')
            original=dict(bridge._turns['conversation'])
            try:
                result=await bridge.read_report_page('conversation',REF,page,'ui-read')
            finally:
                self.assertEqual(bridge._turns['conversation'],original)
                self.assertEqual([p for p,_ in calls],['/api/master/broker/prepare','/api/master/broker/step','/api/master/broker/finish'])
                self.assertEqual(calls[0][1]['report_body_scope'],'local')
                self.assertEqual(calls[1][1]['tool_call']['action'],'view')
                self.assertEqual(calls[1][1]['tool_call']['reference']['token'],REF)
            return result

    async def test_page_is_local_and_never_replaces_active_chat_turn(self):
        body='<details><summary>未完成范围与完整缺口记录</summary>\n末尾反证\n</details>'
        result=await self.run_read(body+'\n\n---\n\n第 2/5 页。可继续说“查看刚才的报告第 3 页”。')
        self.assertEqual(result,{'content':body,'page':2,'page_count':5})

    async def test_single_page(self):
        self.assertEqual((await self.run_read('全部证据',page=1))['page_count'],1)

    async def test_wrong_page_empty_body_or_refused_read_never_becomes_report(self):
        for text,code,status in [('', 'PUBLIC_OBSERVATION','TOOL_RETURNED'),
                                 ('未执行', 'BAD_PAGE','TOOL_RETURNED'),
                                 ('被拦截', 'REFERENCE_NOT_OWNED','RULE_BLOCKED'),
                                 ('正文\n\n---\n\n第 1/3 页。可继续说下一页','PUBLIC_OBSERVATION','TOOL_RETURNED')]:
            with self.subTest(text=text),self.assertRaises(ValueError):
                await self.run_read(text,code=code,status=status)


class ReportReaderAuthorizationTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.bridge=SimpleNamespace(read_report_page=AsyncMock(return_value={'content':'完整证据','page':1,'page_count':3}))
        self.message=Message('report','c','tool','[ok]',tool_name='stock_report_read',
                             tool_result=json.dumps({'local_result':'旧页'}),
                             metadata=json.dumps({'params':{'reference':REF}}))
        self.service=object.__new__(ChatSessionService)
        self.service._store=SimpleNamespace(get_conversation=lambda cid: SimpleNamespace(id=cid),
                                           get_message=lambda mid:self.message if mid=='report' else None)
        self.service._runtime=SimpleNamespace(stock_bridge=self.bridge)

    async def test_saved_record_supplies_reference_without_history_mutation(self):
        before=vars(self.message).copy()
        await self.service.read_report_page('c','report',1,'ui-read')
        self.bridge.read_report_page.assert_awaited_once_with('c',REF,1,'ui-read')
        self.assertEqual(vars(self.message),before)

    async def test_other_conversation_or_inactive_record_is_rejected(self):
        for cid,active in [('other',True),('c',False)]:
            self.message.active=active
            with self.assertRaises(HTTPException) as exc:
                await self.service.read_report_page(cid,'report',1,'read')
            self.assertEqual(exc.exception.status_code,404)
        self.bridge.read_report_page.assert_not_awaited()

    async def test_unrelated_tool_and_missing_reference_are_rejected(self):
        for tool,meta in [('file_read',self.message.metadata),('stock_report_read','{}')]:
            self.message.tool_name=tool;self.message.metadata=meta
            with self.assertRaises(HTTPException):
                await self.service.read_report_page('c','report',1,'read')
        self.bridge.read_report_page.assert_not_awaited()

    async def test_disconnected_bridge_preserves_saved_page(self):
        self.service._runtime.stock_bridge=None
        with self.assertRaises(HTTPException) as exc:
            await self.service.read_report_page('c','report',1,'read')
        self.assertEqual(exc.exception.status_code,503)
        self.assertEqual(json.loads(self.message.tool_result)['local_result'],'旧页')

    async def test_endpoint_rejects_untrusted_task_and_invalid_page(self):
        # No lifespan: this isolated HTTP test must not build the daily runtime.
        with patch('bff.app.get_chat_service',return_value=self.service):
            client=TestClient(app)
            base={'message_id':'report','page':1,'request_id':'read'}
            for changed in [{'page':0},{'page':True},{'page':'2'},{'reference':'other-task'}]:
                result=client.post('/api/conversations/c/report-page',json={**base,**changed})
                self.assertEqual(result.status_code,422)
            result=client.post('/api/conversations/c/report-page',json=base)
            self.assertEqual(result.status_code,200)
            self.assertEqual(result.json()['content'],'完整证据')
            client.close()
