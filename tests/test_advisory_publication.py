"""Default publication really reviews once; advice and failed execution differ."""
import json
import unittest
from unittest.mock import patch

from core import config
from core.research_review import fingerprint
from tests import test_research_review as reviews, test_research_session as sessions


class AdvisoryPublicationTests(unittest.IsolatedAsyncioTestCase):
    async def publish(self, agent, draft):
        call = {'id':'publish','function':{'name':'brief_publish','arguments':draft}}
        return [e async for e in agent._execute_tools([call],'test',[
            {'role':'system','content':'原权限'}, {'role':'user','content':'原问题'}],allowed={'brief_publish'})]

    async def test_default_reviews_canonical_draft_once_and_publishes_with_advice(self):
        issue = {'section':'结论','claim':'原结论待核','reason':'<待补证>',
                 'evidence':'已有来源','action':'核对适用期间','severity':'blocking'}
        with patch.object(config,'PUBLISH_REVIEW_ENFORCE',False):
            files,run,draft,agent,calls = await reviews.ReviewTests().setup_review([
                {'issues':[issue],'checks':['已检查期间'],'read_requests':[
                    {'source_id':'advice-only','terms':['补查建议']}]}])
            run.record['phase'] = '分析'
            draft.pop('review')
            events = await self.publish(agent,draft)
            self.assertEqual(len(calls),1)
            self.assertTrue(run.published)
            self.assertEqual(run.record['phase'],'交付')
            self.assertFalse(run.pending_evidence)
            self.assertEqual(events[-1].data['status'],'ok')
            html = files.files[run.published['path']]
            self.assertIn('<details><summary>待核事项',html)
            self.assertIn('&lt;待补证&gt;',html)
            self.assertNotIn('原结论待核',files.files[run.record['directory']+'/model-original.md'])
            evidence = json.loads(files.files[run.record['directory']+'/evidence.json'])
            self.assertEqual(evidence['separate_review']['status'],'COMPLETE')
            self.assertFalse(evidence['separate_review']['semantic_certification'])
            await self.publish(agent,draft)
            self.assertEqual(len(calls),1)

    async def test_failed_or_unknown_review_keeps_readable_unpublished_draft(self):
        for output, status in [(RuntimeError('timeout'),'UNKNOWN'),({'issues':[],'checks':[]},'FAILED')]:
            with self.subTest(status=status), patch.object(config,'PUBLISH_REVIEW_ENFORCE',False):
                files,run,draft,agent,calls = await reviews.ReviewTests().setup_review([output])
                events = await self.publish(agent,draft)
                self.assertEqual(events[-1].data['status'],'error')
                self.assertIsNone(run.published)
                self.assertEqual(next(iter(run.review_records.values()))['status'],status)
                html = files.files[run.record['draft']['path']]
                self.assertIn('草稿 · 单独核对未完成',html)
                self.assertNotIn(run.record['directory']+'/report.html',files.files)
                self.assertEqual(run.record['status'],'DELIVERY_INCOMPLETE')
                self.assertEqual(len(calls),1)

    async def test_excerpt_reference_requires_returned_verified_snapshot_and_preserves_quote(self):
        _,run,draft = await sessions.ResearchSessionTests().prepare()
        key = draft['sources'][0]['id']
        selection = {'page':1,'start':0,'end':8}
        draft['sources'] = [{'id':key,'excerpt':selection}]
        # Downloaded source pages alone are insufficient.
        with self.assertRaisesRegex(ValueError,'实际返回'):
            await run.publication_input(draft)
        await run.source_excerpts({'stdout':json.dumps({'source_excerpts':[
            {'source_id':key,'sha256':'a'*64,**selection,'quote':'营业收入100元'}]})})
        resolved = await run.publication_input(draft)
        self.assertEqual(resolved['sources'][0]['quote'],'营业收入100元')
        self.assertIn('第1页',resolved['sources'][0]['locator'])
        self.assertNotIn('quote',draft['sources'][0])
        draft['sources'][0]['quote'] = '改写引句'
        with self.assertRaisesRegex(ValueError,'冲突'):
            await run.publication_input(draft)
        draft['sources'][0].pop('quote')
        run.sources[key]['sha256'] = 'b'*64
        with self.assertRaisesRegex(ValueError,'实际返回'):
            await run.publication_input(draft)
