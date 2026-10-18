"""Run inside the existing search image: python -m unittest test_fetch_transport."""
import gzip
import unittest
import httpx
import app


class FetchTransportTests(unittest.IsolatedAsyncioTestCase):
    def test_pdf_links_preserve_actual_targets_and_skip_non_http_links(self):
        links = app._document_links('''<a href="../notice.PDF?download=1">公告原文</a>
            <a href="../notice.PDF?download=1">重复</a><a href="javascript:bad.pdf">不执行</a>
            <a href="other.html">普通导航</a>''', 'https://example.org/reports/page.html')
        self.assertEqual(links, [{'url': 'https://example.org/notice.PDF?download=1',
            'label': '公告原文', 'found_on': 'https://example.org/reports/page.html'}])

    async def test_empty_or_corrupt_pdf_is_a_specific_failure(self):
        from fastapi import HTTPException
        for raw in (b'', b'%PDF-1.7\nnot a valid pdf'):
            transport = httpx.MockTransport(lambda req: httpx.Response(200,
                headers={'content-type': 'application/pdf'}, content=raw))
            async with httpx.AsyncClient(transport=transport) as client:
                old = app._client; app._client = client
                try:
                    with self.assertRaises(HTTPException) as caught:
                        await app.web_fetch(app.WebFetchRequest(url='https://example.org/report.pdf'))
                    self.assertEqual(caught.exception.status_code, 422)
                    self.assertIn('为空或损坏', caught.exception.detail)
                finally:
                    app._client = old

    async def test_compressed_body_is_decoded_once_and_saved(self):
        raw = b'<html><head><title>Report</title></head><body><p>Revenue 100</p><a href="notice.pdf">PDF</a></body></html>'
        transport = httpx.MockTransport(lambda req: httpx.Response(200,
            headers={'content-type': 'text/html', 'content-encoding': 'gzip'},
            content=gzip.compress(raw)))
        async with httpx.AsyncClient(transport=transport) as client:
            old = app._client
            app._client = client
            try:
                result = await app.web_fetch(app.WebFetchRequest(url='https://example.org/report', include_file=True))
            finally:
                app._client = old
        self.assertIn('Revenue 100', result['content'])
        import base64
        self.assertEqual(base64.b64decode(result['_source_file']['base64']), raw)
        self.assertEqual(result['document_links'][0]['url'], 'https://example.org/notice.pdf')
