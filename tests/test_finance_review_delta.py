import copy
import json
import unittest

from plugins.finance.review_delta import (changes, mark_numeric_advice, material_identity,
                                          plan_revision, table_context, track_findings)


class ReviewDeltaTests(unittest.TestCase):
    def setUp(self):
        ref = 'S1234567890abcdef'
        other = 'S0000000000000000'
        self.candidate = {'title':'行业观察','as_of':'2025-12-31','stance':'继续研究',
            'markdown':'# 结论\n先看甲公司。\n## 反证\n乙公司现金流更强。\n## 不变说明\n共同期间为2025年。',
            'sources':[{'id':ref,'quote':'2025年收入100元','locator':'第1页'},
                       {'id':other,'quote':'乙公司2025年现金流为正'}], 'review':{}}
        self.payload = {'question':'哪些值得看看','candidate':self.candidate,'methods':{},
            'decision_claims':[{'stage_id':'stage','claim_id':'claim','claim':{
                'text':'先看甲公司。','evidence_refs':['role:batch:finance_evidence:0:0']}}],
            'revision_context':None, 'previous_findings':[], 'decision_surface':{},
            'publication_context':{}, 'cited_page_context':[],
            'evidence':{'sources':{ref:{'sha256':'a'*64,'received':['2025年收入100元']},
                other:{'sha256':'b'*64,'received':['乙公司2025年现金流为正','未选用的额外摘录']}}},
            'artifacts':{'role:batch:finance_analysis:0':{'kind':'role_claims','content':{'claims':[]}},
                'role:batch:finance_evidence:0:0':{'kind':'source_excerpt',
                    'excerpt':{'source_id':ref,'quote':'2025年收入100元'}},
                'role:batch:finance_evidence:0:1':{'kind':'source_excerpt',
                    'excerpt':{'source_id':other,'quote':'乙公司2025年现金流为正'}}}}
        self.baseline = {'key':'previous','candidate':copy.deepcopy(self.candidate),
                         'material_sha256':material_identity(self.payload),'evidence_sha256':'evidence'}

    def test_first_review_keeps_candidate_and_only_its_actual_material(self):
        payload = copy.deepcopy(self.payload)
        payload['evidence']['sources']['Sffffffffffffffff'] = {
            'sha256':'c'*64,'received':['未采用的来源']}
        payload['artifacts']['role:other:finance_evidence:0:0'] = {
            'kind':'source_excerpt','excerpt':{'source_id':'Sffffffffffffffff','quote':'未采用的来源'}}
        packet, scope = plan_revision(payload,self.candidate,None)
        self.assertEqual(scope['mode'],'full')
        self.assertEqual(packet['candidate'],self.candidate)
        self.assertNotIn('Sffffffffffffffff',packet['evidence']['sources'])
        self.assertNotIn('role:other:finance_evidence:0:0',packet['artifacts'])
        self.assertEqual(packet['evidence']['sources']['S0000000000000000']['received'],
                         ['乙公司2025年现金流为正'])

    def test_narrative_revision_sends_diff_without_whole_candidate(self):
        revised = copy.deepcopy(self.candidate)
        revised['markdown'] = revised['markdown'].replace('先看甲公司。','优先继续核查甲公司。')
        packet, scope = plan_revision(self.payload,revised,self.baseline)
        self.assertEqual(scope['mode'],'local')
        self.assertEqual(scope['source_ids'],['S1234567890abcdef'])
        self.assertNotIn('S0000000000000000',packet['original_material'])
        self.assertNotIn('candidate',packet)
        self.assertNotIn('conclusion_context',packet)
        self.assertNotIn('共同期间为2025年',json.dumps(packet,ensure_ascii=False))
        self.assertEqual(packet['decision_claims'][0]['claim']['text'],'先看甲公司。')
        self.assertEqual(packet['original_material'][self.candidate['sources'][0]['id']]['received'],
                         ['2025年收入100元'])

    def test_local_revision_limits_claims_and_findings_to_the_change(self):
        payload = copy.deepcopy(self.payload)
        payload['decision_claims'].append({'stage_id':'other','claim_id':'other','claim':{
            'text':'乙公司暂不关注','entity':'乙公司',
            'evidence_refs':['role:batch:finance_evidence:0:1']}})
        payload['previous_findings'] = [
            {'section':'# 结论','claim':'先看甲公司。','evidence':'S1234567890abcdef'},
            {'section':'## 反证','claim':'乙公司现金流更强','evidence':'S0000000000000000'}]
        payload['open_findings'] = [
            {'id':'related','section':'# 结论','claim':'先看甲公司。','state':'unverified'},
            {'id':'unrelated','section':'## 反证','claim':'乙公司现金流更强','state':'unverified'}]
        self.baseline['material_sha256'] = material_identity(payload)
        revised = copy.deepcopy(self.candidate)
        revised['markdown'] = revised['markdown'].replace('先看甲公司。','优先继续核查甲公司。')
        packet, scope = plan_revision(payload,revised,self.baseline)
        self.assertEqual(scope['mode'],'local')
        self.assertEqual([row['claim_id'] for row in packet['decision_claims']],['claim'])
        self.assertEqual([row['id'] for row in packet['open_findings']],['related'])
        self.assertEqual(len(packet['previous_findings']),1)
        self.assertEqual(scope['source_ids'],['S1234567890abcdef'])

    def test_changed_date_expands_without_unrelated_role_material(self):
        revised = {**self.candidate,'as_of':'2026-01-31'}
        packet, scope = plan_revision(self.payload,revised,self.baseline)
        self.assertEqual(scope['mode'],'expanded')
        self.assertNotIn('candidate',packet)
        self.assertIn('role:batch:finance_evidence:0:0',packet['verified_artifacts'])
        self.assertNotIn('role:batch:finance_analysis:0',packet['verified_artifacts'])
        self.assertEqual(packet['outline'],['# 结论','## 反证','## 不变说明'])

    def test_unrelated_reading_and_internal_notes_do_not_restart_full_review(self):
        payload = {**self.payload,'received_sha256':'newly-read-unrelated-page',
                   'decision_claims':[{'stage_id':'stage','claim_id':'claim'}]}
        self.assertEqual(material_identity(payload),self.baseline['material_sha256'])
        noted = {**self.candidate,'editorial_notes':['排版说明']}
        packet, scope = plan_revision(payload,noted,self.baseline)
        self.assertEqual((packet,scope['mode']),({},'reuse'))

    def test_removed_counterevidence_keeps_the_original_section(self):
        result = changes({'markdown':'# 排序\n一拖优先\n## 反证\n潍柴现金流更强'},
                         {'markdown':'# 排序\n一拖优先'})
        diff = result['markdown'][0]
        self.assertIn('潍柴现金流更强',diff['before'])
        self.assertEqual(diff['after'],[])

    def test_product_table_column_survives_numeric_reinterpretation(self):
        heading = '分产品 营业收入 营业成本 毛利率 收入同比 成本同比 毛利率同比'
        quote = '989,271 850,378 14.04 -9.72 -10.20'
        text = heading+'\n农业机械 '+quote+' 增加0.46个百分点'
        citation = {'id':'S1','quote':quote}
        for revision in ('毛利率同比减少10.20个百分点','成本同比-10.20%，毛利率增加0.46个百分点'):
            rows = table_context({'S1':[text]},[citation],{'diff':{'markdown':revision}})
            self.assertIn(heading,rows[0]['text'])
            self.assertIn('增加0.46个百分点',rows[0]['text'])
        issue = {'reason':'毛利率下降10.20个百分点','action':'调整结论'}
        mark_numeric_advice([issue],rows)
        self.assertIn('待核实',issue['numeric_basis'])

    def test_missing_update_preserves_open_finding(self):
        rows = track_findings([], [{'section':'利润','claim':'成本同比下降'}], [])
        self.assertEqual(track_findings(rows,[],[]),rows)
        resolved = track_findings(rows,[],[{'id':rows[0]['id'],'state':'addressed','reason':'已对照原表'}])
        self.assertEqual(resolved[0]['state'],'addressed')
        optional = track_findings([], [{'section':'补充','claim':'可选建议',
                                       'category':'optional','severity':'advisory'}], [])
        self.assertEqual(optional[0]['category'],'optional')
        self.assertEqual(optional[0]['severity'],'advisory')
