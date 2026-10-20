import importlib.util
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

root=Path('skills/runner/packages/harness-public-sources-v3/scripts')
def load(name):
    spec=importlib.util.spec_from_file_location(name,root/(name+'.py'))
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module);return module


class QuoteReceiptTests(unittest.TestCase):
    def test_raw_identity_price_time_spans_and_mismatch(self):
        fields=['']*31
        fields[0:5]=['51','测试公司','000001','12.34','12.00']
        fields[30]='20261020161500'
        text='v_sz000001="'+'~'.join(fields)+'";'
        with TemporaryDirectory() as temp:
            p=Path(temp)/'Sactual.json';p.write_text(json.dumps({'sha256':'a'*64,'pages':[{'page':1,'text':text}]}),encoding='utf-8')
            quote=load('read_quote').read_quote(p,'SZ000001')
            self.assertEqual(quote['raw_values']['price'],'12.34')
            self.assertEqual(quote['raw_values']['quote_time'],'20261020161500')
            self.assertNotIn('close',quote['raw_values'])
            for row in quote['source_excerpts']:self.assertEqual(row['quote'],text[row['start']:row['end']])
            with self.assertRaises(ValueError):load('read_quote').read_quote(p,'SZ000002')
            p.write_text(json.dumps({'sha256':'a'*64,'pages':[{'page':1,'text':'Copyright only'}]}),encoding='utf-8')
            with self.assertRaises(ValueError):load('read_quote').read_quote(p,'SZ000001')

    def test_public_entry_reuses_verified_symbol_contract(self):
        urls=load('source_urls')
        self.assertEqual(urls.source_url('quote',symbol='SZ000001'),'https://qt.gtimg.cn/q=sz000001')
        with self.assertRaises(ValueError):urls.source_url('quote',symbol='a guessed company')

