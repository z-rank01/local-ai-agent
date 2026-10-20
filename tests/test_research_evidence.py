import base64
import json
import unittest
from core.research_evidence import evidence_steps, financial_read_step, catalogue
from tests.test_research_session import Files
from core.research_session import ResearchSession


class EvidenceTests(unittest.IsolatedAsyncioTestCase):
    def test_explicit_provider_entries_and_finance_version_directory(self):
        steps = evidence_steps({'kind':'finance', 'symbol':'SH688178'})
        self.assertEqual([s['name'] for s in steps], ['file_read','web_fetch','web_fetch'])
        self.assertIn('ann?', steps[1]['params']['url'])
        self.assertIn('code=SH688178', steps[2]['params']['url'])
        for request in ({'kind':'notice','article':'guessed'}, {'kind':'finance','symbol':'万德斯'},
                        {'kind':'notices','symbol':'SH688178','start':'2026-10-20','end':'2025-01-01'}):
            with self.assertRaises(ValueError): evidence_steps(request)

    def test_catalogue_keeps_version_and_identity_without_claiming_full_read(self):
        rows = [{'title':title,'notice_date':'2026-04-29','art_code':'AN123','codes':[{'stock_code':'688178'}]}
                for title in ('2025年度业绩快报','2025年年度报告','更正公告')]
        actual = catalogue([{'text':json.dumps({'data':{'list':rows,'total_hits':103,'page_index':1}})}])
        self.assertEqual(actual['entries'],rows)
        self.assertEqual(actual['total_hits'],103)

    def test_field_reader_keeps_explicit_keys_and_is_python_not_shell(self):
        request={'where':{'SECUCODE':'688178.SH','REPORT_DATE':'2026-06-30 00:00:00'},'fields':['PARENTNETPROFIT']}
        code=financial_read_step(request,'/workspace/source.json')['params']['code']
        compile(code,'reader','exec')
        self.assertIn('PARENTNETPROFIT',code)
        self.assertNotIn('urlopen',code)

    def test_finance_reads_actual_selected_report_ids_not_inferred_titles(self):
        steps=evidence_steps({'kind':'finance','symbol':'SZ000651','report_articles':['AN123','AN123','AN456']})
        self.assertEqual(len(steps),5)
        self.assertIn('AN456',steps[-1]['params']['url'])
        self.assertIn('v4/references/finance.md',steps[0]['params']['path'])
        with self.assertRaises(ValueError):evidence_steps({'kind':'finance','symbol':'SZ000651','report_articles':['invented']})

    async def test_request_dedup_failure_and_real_receipt_advance(self):
        run=ResearchSession(Files(),conversation_id='c',model='m',allowed=True)
        request={'kind':'notice','article':'AN123'}
        with self.assertRaises(ValueError):
            await run.begin({'scope':'x','plan':['x'],'evidence_requests':[request]})
        run.methods.add('/workspace/skills/harness-stock-research-v3/references/event.md')
        await run.begin({'scope':'x','plan':['x'],'evidence_requests':[request]})
        await run.begin({'phase':'获取','evidence_requests':[request]})
        self.assertEqual(len(run.pending_evidence),1)
        step=run.pending_evidence[0]
        await run.complete_evidence(step,{'error':'HTTP 403'},'error')
        self.assertEqual(next(iter(run.evidence_requests.values()))['status'],'FAILED')
        await run.begin({'phase':'获取','evidence_requests':[request]})
        self.assertFalse(run.pending_evidence)

    async def test_actual_unique_attachment_is_queued_not_guessed(self):
        run=ResearchSession(Files(),conversation_id='c',model='m',allowed=True)
        run.methods.add('method')
        await run.begin({'scope':'x','plan':['x'],'evidence_requests':[{'kind':'notice','article':'AN123'}]})
        result=await run.save_source({'url':'https://example.org/detail','title':'公告',
            'sha256':'a'*64,'fetched_at':'2026-10-20','page_count':1,'total_chars':3,
            'media_type':'text/html','content':'公告原文',
            'document_links':[{'url':'https://example.org/actual.pdf'}],
            '_source_file':{'base64':base64.b64encode(b'raw').decode(),'pages':[{'page':1,'text':'公告原文'}]}})
        await run.complete_evidence(run.pending_evidence[0],result,'ok')
        await run.observe({'name':'web_fetch','params':{},'status':'ok','result':result})
        self.assertEqual(run.pending_evidence[0]['params']['url'],'https://example.org/actual.pdf')
        self.assertEqual(run.read_text[result['source_id']],['公告原文'])
        self.assertIn('evidence-index.json',' '.join(run.router.files))
        before=json.dumps(run.evidence_index(),ensure_ascii=False,sort_keys=True)
        self.assertIn('公告原文',run.guidance())
        self.assertEqual(before,json.dumps(run.evidence_index(),ensure_ascii=False,sort_keys=True))
        with self.assertRaises(ValueError):
            await run.begin({'phase':'分析','evidence_notes':[{'source_id':'other','document_kind':'公告'}]})
        await run.begin({'phase':'分析','evidence_notes':[{'source_id':result['source_id'],
            'document_kind':'公告','disclosed_at':'2026-10-09','period':'2026','note':'尚未核对后续披露'}]})
        self.assertIn('尚未核对后续披露',run.guidance())
        self.assertNotIn('已有足够材料',run.guidance())

    async def test_manual_detail_attachment_failure_is_visible_and_not_requeued(self):
        run=ResearchSession(Files(),conversation_id='c',model='m',allowed=True)
        run.methods.add('method');await run.begin({'scope':'x','plan':['x']})
        result={'url':'https://example.org/detail','document_links':[{'url':'https://example.org/report.pdf'}]}
        run.queue_document(result)
        self.assertEqual(run.pending_evidence[0]['name'],'web_fetch')
        step=run.pending_evidence[0]
        await run.complete_evidence(step,{'error':'HTTP 403'},'error')
        run.queue_document(result)
        self.assertFalse(run.pending_evidence)
        self.assertEqual(run.evidence_requests[step['request_key']]['status'],'FAILED')
        run.queue_document({**result,'document_links':[{'url':'a'},{'url':'b'}]})
        self.assertFalse(run.pending_evidence)

    async def test_selected_financial_report_retains_terms_until_actual_pdf_receipt(self):
        run=ResearchSession(Files(),conversation_id='c',model='m',allowed=True)
        run.methods.add('method')
        await run.begin({'scope':'x','plan':['x'],'evidence_requests':[{'kind':'finance','symbol':'SZ000651',
            'report_articles':['AN123'],'terms':['应付股利']}]})
        # Existing directory/field tools have completed; no report text was read.
        for _ in range(3):await run.complete_evidence(run.pending_evidence[0],{},'ok')
        url=run.pending_evidence[0]['params']['url']
        detail=await run.save_source({'url':url,'title':'报告详情','sha256':'b'*64,'fetched_at':'2026-10-20',
            'page_count':1,'total_chars':2,'media_type':'application/json','content':'详情',
            'document_links':[{'url':'https://example.org/full.pdf'}],
            '_source_file':{'base64':base64.b64encode(b'raw').decode(),'pages':[{'page':1,'text':'详情'}]}})
        # Production order: complete the queued step, then observe tool_end.
        await run.complete_evidence(run.pending_evidence[0],detail,'ok')
        await run.observe({'name':'web_fetch','params':{'url':url},'status':'ok','result':detail})
        request=run.evidence_requests[run.pending_evidence[0]['request_key']]['request']
        self.assertEqual(request['terms'],['应付股利'])
        self.assertNotIn('应付股利已确认',str(run.read_text))
        result=await run.save_source({'url':'https://example.org/full.pdf','title':'报告',
            'sha256':'a'*64,'fetched_at':'2026-10-20','page_count':2,'total_chars':20,
            'media_type':'application/pdf','content':'[第1页]封面',
            '_source_file':{'base64':base64.b64encode(b'raw').decode(),
                'pages':[{'page':1,'text':'封面'},{'page':70,'text':'应付股利已确认'}]}})
        await run.complete_evidence(run.pending_evidence[0],result,'ok')
        self.assertEqual(run.pending_evidence[0]['name'],'code_exec')
        self.assertNotIn('应付股利已确认',str(run.read_text))

    async def test_program_expansion_is_visible_before_next_model_request(self):
        from types import SimpleNamespace
        from core.agent import Agent
        from tests import test_research_session as session_tests
        files,run,_=await session_tests.ResearchSessionTests().prepare()
        await run.begin({'phase':'获取','evidence_requests':[{'kind':'notice','article':'AN123'}]})
        actual=[]
        class Router:
            research=run
            async def dispatch(self,name,params,session):
                actual.append((name,params))
                return {'error':'HTTP 403','url':params['url']}
        captured=[]
        class Model:
            async def chat_stream_with_tools(self,messages,tools=None):
                captured.extend(messages)
                raise RuntimeError('diagnostic stop')
                yield
        async def process(messages):return messages
        agent=Agent(llm=Model(),router=Router(),registry=SimpleNamespace(get_definitions=lambda **kw:[
            {'type':'function','function':{'name':n}} for n in ('web_fetch','research_begin','brief_publish')]),
            audit=SimpleNamespace(record=lambda *a:None),context_mgr=SimpleNamespace(process=process),
            prompt_builder=SimpleNamespace(build=lambda **kw:'system'),max_rounds=1)
        events=[e async for e in agent.run([{'role':'user','content':'原问题'}])]
        self.assertEqual(len(actual),1)
        self.assertTrue(any(e.kind=='tool_start' and e.data['name']=='web_fetch' for e in events))
        self.assertIn('AN123',json.dumps(captured))
        self.assertFalse(run.pending_evidence)

    async def test_selected_citation_is_actually_returned_before_registering_read(self):
        import tempfile
        from pathlib import Path
        from contextlib import redirect_stdout
        from io import StringIO
        from tests import test_research_session as session_tests
        _,run,draft=await session_tests.ResearchSessionTests().prepare()
        source=draft['sources'][0]['id']
        run.read_text[source]=[]
        run._milestones={m for m in run._milestones if not m[0].startswith('read:')}
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)/'source.json'
            p.write_text(json.dumps({'sha256':'a'*64,'pages':[{'page':1,'text':'营业收入100元'}]}),encoding='utf-8')
            run.sources[source]['path']=str(p)
            self.assertTrue(await run.queue_citation_reads(draft))
            self.assertEqual(run.read_text[source],[])
            output=StringIO()
            with redirect_stdout(output):exec(run.pending_evidence[0]['params']['code'],{})
            receipt=json.loads(output.getvalue())
            self.assertEqual(receipt['source_excerpts'][0]['quote'],'营业收入100元')
            self.assertEqual(run.read_text[source],[])
            await run.observe({'name':'code_exec','params':{},'status':'ok','result':{'stdout':output.getvalue()}})
            self.assertEqual(run.read_text[source],['营业收入100元'])
            missing={**draft,'sources':[{'id':source,'locator':'p1','quote':'营业收入200元'}]}
            self.assertTrue(await run.queue_citation_reads(missing))
            output=StringIO()
            with redirect_stdout(output):exec(run.pending_evidence[-1]['params']['code'],{})
            receipt=json.loads(output.getvalue())
            self.assertFalse(receipt['source_excerpts'])
            self.assertEqual(receipt['unmatched'][0]['requested_quote'],'营业收入200元')
