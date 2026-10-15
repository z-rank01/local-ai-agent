import unittest
from core.brief_document import render_document


class BriefDocumentTests(unittest.TestCase):
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
