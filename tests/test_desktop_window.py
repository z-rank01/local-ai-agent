import unittest
from unittest.mock import Mock, patch
from pathlib import Path

from scripts.desktop_window import main, validate_url


class ValidateUrlTest(unittest.TestCase):
    def test_accepts_daily_bff_url(self):
        url = 'http://127.0.0.1:9510/#session=abc123'
        self.assertEqual(validate_url(url), url)

    def test_accepts_bare_root(self):
        self.assertEqual(validate_url('http://127.0.0.1:9510'), 'http://127.0.0.1:9510')

    def test_rejects_external_host(self):
        with self.assertRaises(ValueError):
            validate_url('http://example.com:9510/')

    def test_rejects_non_http(self):
        with self.assertRaises(ValueError):
            validate_url('file:///etc/passwd')

    def test_rejects_other_port(self):
        with self.assertRaises(ValueError):
            validate_url('http://127.0.0.1:8765/')

    def test_rejects_path(self):
        with self.assertRaises(ValueError):
            validate_url('http://127.0.0.1:9510/api/status')


class MainTest(unittest.TestCase):
    def test_browser_cache_uses_private_runtime_not_current_directory(self):
        webview = Mock()
        with patch.dict('sys.modules', {'webview': webview}):
            self.assertEqual(main(['http://127.0.0.1:9510/']), 0)
        options = webview.start.call_args.kwargs
        self.assertTrue(options['private_mode'])
        self.assertEqual(Path(options['storage_path']), Path(__file__).resolve().parents[1] / 'data/private/desktop-webview')

    def test_usage_error_without_args(self):
        self.assertEqual(main([]), 2)

    def test_rejects_bad_url_before_importing_webview(self):
        self.assertEqual(main(['http://example.com/']), 2)


if __name__ == '__main__':
    unittest.main()
