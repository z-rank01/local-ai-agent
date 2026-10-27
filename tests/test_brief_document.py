import unittest
import json
from hashlib import sha256
from plugins.finance.brief_document import render_document


class BriefDocumentTests(unittest.TestCase):
    def test_utc_generation_time_is_displayed_in_explicit_beijing_time(self):
        html = render_document('原稿不改。', title='测试', generated_at='2026-10-18T14:42:00+00:00',
            model='selected', material_sha256='a'*64)
        self.assertIn('2026-10-18 22:42（北京时间）', html)
        self.assertIn('原稿不改。', html)
    def render(self, text):
        return render_document(text, title='测试', generated_at='2026-10-15', model='试点', material_sha256='a'*64)

    def test_retains_prose_and_explicit_scope(self):
        html = self.render('## 等待验证\n\n影响不确定。\n\n[来源](https://pdf.dfcfw.com/a.pdf)')
        self.assertIn('<h2>等待验证</h2>', html)
        self.assertIn('影响不确定。', html)
        self.assertIn('未进行新资料采集', html)
        self.assertIn('href="https://pdf.dfcfw.com/a.pdf"', html)

    def test_no_active_html_remote_images_or_local_links(self):
        html = self.render('<script>alert(1)</script>\n\n![图](https://x.test/a.png)\n\n[文件](file:///C:/secret)')
        self.assertNotIn('<script>', html)
        self.assertNotIn('<img', html)
        self.assertNotIn('href="file:', html)
        self.assertIn('default-src', html)

    def test_empty_rejected(self):
        with self.assertRaises(ValueError): self.render('')

    def test_explicit_scope_and_material_provenance_are_display_only(self):
        html = render_document('正文保持。', title='测试', generated_at='2026-10-15',
            model='试点', material_sha256='a'*64, scope_label='综合选股参考',
            as_of='2026-10-15', authorship='模型生成 · 人工校订',
            material_note='人工补查 <script>不执行</script>；未完成自动研究。')
        self.assertIn('name="brief-scope" content="综合选股参考"', html)
        self.assertIn('资料时点：2026-10-15', html)
        self.assertIn('&lt;script&gt;', html)
        self.assertNotIn('<script>', html)
        self.assertNotIn('未进行新资料采集', html)
        self.assertIn('正文保持。', html)
        self.assertIn('name="brief-authorship" content="模型生成 · 人工校订"', html)

    def test_layout_metadata_and_program_charts(self):
        calc = dict(kind='EVENT_SCALE', version='event-scale-calculation-v1', symbol='688178',
                    annual_period='20251231', reader_revision='a'*64, amount_yuan='300',
                    annual_revenue_yuan='1000', ratio='0.3', percent='30',
                    service_mean=dict(service_years='3', mean_amount_yuan='100'))
        packet = {'tasks': [{'program_calculations': [calc]}]}
        digest = sha256(json.dumps(packet, ensure_ascii=False, sort_keys=True, allow_nan=False).encode()).hexdigest()
        args = dict(title='测试', generated_at='2026-10-15T18:03:33+08:00', model='试点',
                    material_sha256=digest, material={'packet':packet}, edition='v2')
        html = render_document('## 结论：等待验证\n\n原结论。\n\n## 来源与精确数值\n\n原来源。', **args)
        self.assertEqual(html.count('<svg '), 2)
        self.assertIn('class="verdict"', html)
        self.assertIn('id="references"', html)
        self.assertIn('<details open>', html)
        self.assertIn('name="brief-edition" content="v2"', html)
        self.assertIn('2026-10-15 18:03', html)
        self.assertIn('原来源。', html)
        args['material_sha256'] = '0'*64
        with self.assertRaises(ValueError): render_document('正文', **args)

    def test_stance_requires_explicit_metadata(self):
        args = dict(title='测试', generated_at='2026-10-17', model='试点', material_sha256='a'*64)
        plain = render_document('结论：等待验证。', **args)
        self.assertNotIn('<span class="badge stance-badge">', plain)
        shown = render_document('原正文。', stance='等待验证', **args)
        self.assertIn('<span class="badge stance-badge">等待验证</span>', shown)
        with self.assertRaises(ValueError):
            render_document('正文', stance='<script>', **args)
