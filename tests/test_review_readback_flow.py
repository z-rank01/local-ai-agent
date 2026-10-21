import json
import unittest
from unittest.mock import patch

from core import config
from core.research_review import fingerprint, parse_review
from tests import test_research_review as fixtures

_enforcement = None


def setUpModule():
    """These flows only run while the review gate is enforced."""
    global _enforcement
    _enforcement = patch.object(config, 'PUBLISH_REVIEW_ENFORCE', True)
    _enforcement.start()


def tearDownModule():
    _enforcement.stop()


class ReviewReadbackTests(unittest.IsolatedAsyncioTestCase):
    async def test_prior_adverse_claim_retained_without_affirmation_or_bad_fix(self):
        issue={'section':'估值','claim':'缺失价格来源','reason':'缺原文','evidence':'Sactual',
               'action':'unverified replacement suggestion'}
        _,run,draft,agent,calls=await fixtures.ReviewTests().setup_review([
            {'issues':[issue],'checks':['prior reviewer self-confidence']},
            {'issues':[],'checks':['已重新核查']}])
        await fixtures.ReviewTests().review(agent,draft)
        changed={**draft,'markdown':'## 结论\n仍需核对价格'}
        await fixtures.ReviewTests().review(agent,changed)
        second=json.dumps(calls[1][0],ensure_ascii=False)
        self.assertIn('缺失价格来源',second)
        self.assertNotIn('prior reviewer self-confidence',second)
        self.assertNotIn('unverified replacement suggestion',second)

    async def test_requested_read_runs_before_changed_evidence_can_publish(self):
        outputs=[]
        files,run,draft,agent,calls=await fixtures.ReviewTests().setup_review(outputs)
        source=draft['sources'][0]['id']
        outputs.extend([{'issues':[],'checks':['需要实际回读'],
                  'read_requests':[{'source_id':source,'terms':['营业收入'],'pages':None}]},
                 {'issues':[],'checks':['已对照实际返回的原文']}])
        await fixtures.ReviewTests().review(agent,draft)
        self.assertEqual(run.pending_evidence[0]['name'],'code_exec')
        with self.assertRaises(ValueError):run.check_review(draft)
        step=run.pending_evidence[0]
        result={'exit_code':0,'stdout':json.dumps({'source_excerpts':[{'source_id':source,'sha256':'a'*64,
            'page':1,'start':0,'end':8,'quote':'营业收入100元'}]})}
        await run.complete_evidence(step,result,'ok')
        await run.observe({'name':'code_exec','status':'ok','params':{},'result':result})
        with self.assertRaises(ValueError):run.check_review(draft)
        await fixtures.ReviewTests().review(agent,draft)
        run.check_review(draft)
        self.assertEqual(len(calls),2)
        before=len(run.pending_evidence)
        await run.queue_review_reads(parse_review(json.dumps(outputs[0]))['read_requests'])
        self.assertEqual(len(run.pending_evidence),before)

    async def test_failed_review_read_never_becomes_completed_or_retried(self):
        outputs=[]
        _,run,draft,agent,calls=await fixtures.ReviewTests().setup_review(outputs)
        source=draft['sources'][0]['id']
        outputs.append({'issues':[],'checks':['需补读'],
            'read_requests':[{'source_id':source,'terms':['营业收入']}]})
        await fixtures.ReviewTests().review(agent,draft)
        step=run.pending_evidence[0]
        await run.complete_evidence(step,{'error':'read failed'},'error')
        with self.assertRaisesRegex(ValueError,'失败'):run.check_review(draft)
        await run.queue_review_reads(outputs[0]['read_requests'])
        self.assertFalse(run.pending_evidence)
        self.assertEqual(run.evidence_requests[step['request_key']]['status'],'FAILED')

    async def test_unknown_source_is_not_replaced_and_visual_check_precedes_supplier(self):
        _,run,draft,agent,calls=await fixtures.ReviewTests().setup_review([
            {'issues':[],'checks':['需补读'],'read_requests':[{'source_id':'unknown','terms':['x']}]}])
        with self.assertRaisesRegex(ValueError,'本轮实际来源'):await fixtures.ReviewTests().review(agent,draft)
        self.assertFalse(run.pending_evidence)
        self.assertEqual(run.review_records[fingerprint(draft)]['status'],'FAILED')
        # An empty charts list is no longer a blocking layout error.
        _,run,draft,agent,calls=await fixtures.ReviewTests().setup_review([
            {'issues':[],'checks':['无图也可交付']}])
        draft['charts']=[]
        await fixtures.ReviewTests().review(agent,draft)
        self.assertEqual(len(calls),1)

    def test_no_chart_needs_no_reason_and_numeric_error_is_actionable(self):
        from core.research_visuals import validate_visual_plan, number
        from decimal import InvalidOperation
        validate_visual_plan({'charts':[]})
        validate_visual_plan({'charts':[],'visual_limitations':'资料只有定性观点，不绘制假数据'})
        with self.assertRaisesRegex(InvalidOperation,'数值格式无效'):number('not available')

    async def test_tool_returns_pending_not_publication_before_review_read(self):
        from unittest.mock import AsyncMock
        outputs=[]
        _,run,draft,agent,calls=await fixtures.ReviewTests().setup_review(outputs)
        outputs.append({'issues':[],'checks':['需要补读'],
            'read_requests':[{'source_id':draft['sources'][0]['id'],'terms':['营业收入']}]})
        run.publish=AsyncMock(side_effect=AssertionError('published before read'))
        call={'id':'publish','function':{'name':'brief_publish','arguments':draft}}
        events=[e async for e in agent._execute_tools([call],'s',[],allowed={'brief_publish'})]
        self.assertEqual(events[-1].data['result']['status'],'EVIDENCE_PENDING')
        run.publish.assert_not_awaited()
        self.assertEqual(len(calls),1)

    async def test_chart_error_points_to_the_unbound_value(self):
        from core.research_visuals import validate_publication
        _,run,draft,_,_=await fixtures.ReviewTests().setup_review([])
        draft['charts'][0]['items'][0]['value']='200'
        with self.assertRaisesRegex(ValueError,r'charts\[0\].items\[0\].*200'):
            validate_publication(draft,run.sources,run.read_text)

    def test_read_protocol_rejects_arbitrary_paths_and_accepts_no_page_constraint(self):
        value={'issues':[],'checks':['checked'],'read_requests':[{'source_id':'S','terms':['text'],'pages':None}]}
        self.assertNotIn('pages',parse_review(json.dumps(value))['read_requests'][0])
        value['read_requests'][0]['path']='/private/key'
        with self.assertRaises(ValueError):parse_review(json.dumps(value))

