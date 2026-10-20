import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('public_reader_v2',ROOT/'skills/runner/packages/harness-public-sources-v2/scripts/read_json.py')
reader=importlib.util.module_from_spec(spec)
spec.loader.exec_module(reader)


class ReaderV2Tests(unittest.TestCase):
    def test_complete_pages_short_fields_available_dates_and_precise_raw_values(self):
        raw='{"data":[{"SECUCODE":"A","REPORT_DATE":"2026-06-30","amount":123456789.123456789,"absent":null,"unused":"'+('x'*2000)+'"},{"SECUCODE":"A","REPORT_DATE":"2025-12-31","amount":10}]}'
        document={'content':raw[:50],'pages':[{'page':1,'text':raw}],'sha256':'a'*64,'next_offset':50}
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)/'Sactual.json';p.write_text(json.dumps(document),encoding='utf-8')
            result=reader.read_rows(p,{'SECUCODE':'A','REPORT_DATE':'2026-06-30'},['amount','absent','missing'],1)
        self.assertEqual(result['available_periods'],['2026-06-30','2025-12-31'])
        self.assertIn('unused',result['available_fields'])
        self.assertEqual(result['rows'][0]['raw_values']['amount'],'123456789.123456789')
        self.assertIsNone(result['rows'][0]['values']['absent'])
        self.assertEqual(result['rows'][0]['missing_fields'],['missing'])
        self.assertLess(sum(len(r['quote']) for r in result['source_excerpts']),150)
        for span in result['source_excerpts']:
            self.assertEqual(raw[span['start']:span['end']],span['quote'])
        self.assertFalse(any('unused' in s['quote'] for s in result['sources']))

    def test_nested_or_escaped_keys_keep_exact_original_position(self):
        raw=json.dumps({'data':[{'code':'A','period':'2025','value':{'a':'escaped " text'},'null':None}]})
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)/'Ssource.json';p.write_text(json.dumps({'sha256':'b'*64,'pages':[{'page':1,'text':raw}]}),encoding='utf-8')
            result=reader.read_rows(p,{'code':'A','period':'2025'},['value','null'])
        self.assertEqual(len(result['source_excerpts']),4)
        for span in result['source_excerpts']:self.assertEqual(raw[span['start']:span['end']],span['quote'])
