"""Actual ratio/percentage units must agree with publication calculations."""
import copy
import unittest
from decimal import Decimal

from plugins.finance.publication import validate_publication


class QuantityRatioTests(unittest.TestCase):
    def prepare(self, unit='倍', left=('1','万元'), right=('400','元')):
        quote = '原文金额1万元、100元、400元，比例25%。'
        source = 'S-original'
        candidate = {'title':'比较', 'markdown':'有边界的研究', 'as_of':'2026-10-30',
                     'stance':'等待验证', 'sources':[{'id':source,'quote':quote}],
                     'calculations':[{'operation':'divide','unit':unit,'inputs':[
                         {'value':left[0],'unit':left[1],'period':'2026-09-30','source':source},
                         {'value':right[0],'unit':right[1],'period':'2026-09-30','source':source}]}]}
        return candidate, {source:{}}, {source:[quote]}

    def test_mixed_money_ratio_is_normalized_without_rewriting_submission(self):
        candidate, sources, text = self.prepare()
        original = copy.deepcopy(candidate)
        checked = validate_publication(candidate, sources, text)
        self.assertEqual(Decimal(checked['calculations'][0]['result']), Decimal('25'))
        self.assertEqual(candidate, original)
        candidate['calculations'][0]['result'] = '0.0025'
        with self.assertRaisesRegex(ValueError, r'calculations\[0\].*计算结果不一致'):
            validate_publication(candidate, sources, text)

    def test_percentage_result_is_a_percentage_number_not_a_fraction(self):
        candidate, sources, text = self.prepare('%', ('100','元'), ('400','元'))
        checked = validate_publication(candidate, sources, text)
        self.assertEqual(Decimal(checked['calculations'][0]['result']), Decimal('25'))
        self.assertEqual(validate_publication(checked, sources, text), checked)
        candidate['calculations'][0]['result'] = '0.25'
        with self.assertRaisesRegex(ValueError, '结果单位%应为25'):
            validate_publication(candidate, sources, text)

    def test_explicit_unit_conversion_is_not_converted_twice(self):
        candidate, sources, text = self.prepare('倍', ('25','%'))
        candidate['calculations'][0]['inputs'][1] = {
            'value':'100', 'unit':'%/倍', 'period':'不适用', 'source':'constant:百分数换算'}
        checked = validate_publication(candidate, sources, text)
        self.assertEqual(Decimal(checked['calculations'][0]['result']), Decimal('0.25'))
        candidate['calculations'].append({'operation':'sum','unit':'%', 'inputs':[
            {'unit':'%', 'period':'2026-09-30','source':'calculation:0'}]})
        checked = validate_publication(candidate, sources, text)
        self.assertEqual(Decimal(checked['calculations'][1]['result']), Decimal('25'))

    def test_missing_period_and_source_return_input_paths_not_key_errors(self):
        candidate, sources, text = self.prepare()
        del candidate['calculations'][0]['inputs'][0]['period']
        candidate['calculations'][0]['inputs'][1]['source'] = None
        with self.assertRaises(ValueError) as error:
            validate_publication(candidate, sources, text)
        self.assertIn('inputs[0]', str(error.exception))
        self.assertIn('period', str(error.exception))
        self.assertIn('inputs[1]', str(error.exception))
        self.assertIn('source', str(error.exception))

    def test_rate_difference_uses_points_and_preserves_dependencies_and_submission(self):
        from plugins.finance.publication import convert_unit
        candidate,sources,text=self.prepare('%',('100','元'),('400','元'))
        candidate['calculations'].append({'operation':'subtract','unit':'个百分点','inputs':[
            {'source':'calculation:0','period':'当前','unit':'比例'},
            {'source':'constant:基准','period':'不适用','unit':'%','value':'20'}]})
        candidate['charts']=[{'title':'差值','unit':'百分点','period':'当前','items':[{'label':'差值','calculation':1}]}]
        original=copy.deepcopy(candidate)
        result=validate_publication(candidate,sources,text)
        self.assertEqual(Decimal(result['calculations'][1]['result']),Decimal('5'))
        self.assertEqual(Decimal(result['charts'][0]['items'][0]['value']),Decimal('5'))
        self.assertEqual(candidate,original)
        with self.assertRaises(ValueError):convert_unit(25,'%','个百分点')
        candidate['calculations'][1]['result']='0.05'
        with self.assertRaisesRegex(ValueError,'计算结果不一致'):validate_publication(candidate,sources,text)
        del candidate['calculations'][1]['result']
        candidate['calculations'][1]['inputs'][1]['unit']='元'
        with self.assertRaises(ValueError):validate_publication(candidate,sources,text)
