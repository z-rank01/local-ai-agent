import base64
import json
import unittest
from core.research_evidence import evidence_steps, financial_read_step, catalogue
from tests.test_research_session import Files
from core.research_session import ResearchSession


class EvidenceTests(unittest.IsolatedAsyncioTestCase):
    def test_explicit_provider_entries_and_finance_version_directory(self):
        steps = evidence_steps({'kind':'finance', 'symbol':'SH688178'})
        self.assertEqual([s['name'] for s in steps], ['web_fetch','web_fetch'])
        self.assertIn('ann?', steps[0]['params']['url'])
        self.assertIn('code=SH688178', steps[1]['params']['url'])
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
        self.assertEqual(run.pending_evidence[0]['params']['url'],'https://example.org/actual.pdf')
        self.assertEqual(run.read_text[result['source_id']],['公告原文'])
        self.assertIn('evidence-index.json',' '.join(run.router.files))
