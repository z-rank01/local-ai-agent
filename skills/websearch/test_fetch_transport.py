"""Run inside the existing search image: python -m unittest test_fetch_transport."""
import gzip
import unittest
import httpx
import app


class FetchTransportTests(unittest.IsolatedAsyncioTestCase):
    async def test_compressed_body_is_decoded_once_and_saved(self):
        raw = b'<html><head><title>Report</title></head><body><p>Revenue 100</p></body></html>'
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
