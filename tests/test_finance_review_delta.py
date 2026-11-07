import unittest

from plugins.finance.review_delta import changes, mark_numeric_advice, paragraph_packet, table_context, track_findings


class ReviewDeltaTests(unittest.TestCase):
    def test_supplementary_amount_must_match_the_right_row(self):
        old = '第三季度单季收入增长；归母净利润累计下降。'
        new = '第三季度单季收入增长，单季归母净利润-3407万元；前三季度累计归母净利润累计下降。'
        delta = changes({'markdown':'## 公司甲\n'+old},{'markdown':'## 公司甲\n'+new})
        ref = 'S1234567890abcdef'
        issue = {'section':'公司甲','claim':old,'evidence':ref}
        source = {'id':ref,'quote':'单位：元 本报告期 归属于上市公司股东的净利润 -34,069,327.41'}
        candidate = {'sources':[source,{'id':'other','quote':'其他公司'}]}
        packet,scope = paragraph_packet(delta,candidate,[issue],{},'原问题')
        self.assertEqual(scope['source_ids'],[ref])
        self.assertEqual(packet['sources'],[source])
        source['quote'] = source['quote'].replace('净利润','营业收入')
        self.assertIsNone(paragraph_packet(delta,candidate,[issue],{},'原问题'))

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
