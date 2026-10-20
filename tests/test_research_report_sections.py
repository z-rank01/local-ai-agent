import importlib.util
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

path=Path('skills/runner/packages/harness-stock-research-v4/scripts/read_report.py')
spec=importlib.util.spec_from_file_location('report_reader',path)
reader=importlib.util.module_from_spec(spec);spec.loader.exec_module(reader)


class ReportReaderTests(unittest.TestCase):
    def test_common_term_cannot_hide_notes_and_page_directory_not_read_coverage(self):
        pages=[{'page':i,'text':'目录：净资产 100'} for i in range(1,6)]
        pages.append({'page':70,'text':'附注：应付 股利已确认。其他事项未签合同。'})
        with TemporaryDirectory() as tmp:
            p=Path(tmp)/'Sactual.json';p.write_text(json.dumps({'sha256':'a'*64,'pages':pages}),encoding='utf-8')
            result=reader.read_report(p,['净资产','应付股利'],limit=2,context=10)
            self.assertEqual([r['page'] for r in result['source_excerpts']],[1,70])
            self.assertEqual(result['match_pages']['净资产'],[1,2,3,4,5])
            self.assertNotIn(2,[r['page'] for r in result['source_excerpts']])
            for row in result['source_excerpts']:
                text=next(v['text'] for v in pages if v['page']==row['page'])
                self.assertEqual(row['quote'],text[row['start']:row['end']])
            selected=reader.read_report(p,['净资产'],pages=[5])
            self.assertEqual([r['page'] for r in selected['source_excerpts']],[5])
            missing=reader.read_report(p,['不存在的词'])
            self.assertFalse(missing['source_excerpts'])
            self.assertEqual(missing['match_pages'],{'不存在的词':[]})
