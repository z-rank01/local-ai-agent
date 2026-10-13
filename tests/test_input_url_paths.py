import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from core.input_utils import detect_file_paths, detect_urls, ingest_local_file_paths


class URLPathBoundaryTests(unittest.TestCase):
    def test_public_source_urls_are_never_local_paths(self):
        urls = (
            'https://vip.stock.finance.sina.com.cn/corp/go.php/vFD_FinanceSummary/stockid/688178.phtml',
            'https://pdf.dfcfw.com/pdf/H2_AN202610091830262869_1.pdf?1791562831000.pdf',
            'HTTP://example.org/a.txt', 'HTTPS://example.org/C:/report.csv',
        )
        for url in urls:
            for text in (url, '出处：'+url, '"source_url": "'+url+'"', '[来源]('+url+')'):
                with self.subTest(text=text):
                    self.assertEqual(detect_file_paths(text), [])
                    self.assertTrue(detect_urls(text))

    def test_real_local_paths_next_to_urls_remain_detectable(self):
        text = 'https://example.org/a.pdf "C:\\reports\\actual.txt" /workspace/b.csv'
        self.assertEqual(detect_file_paths(text), ['C:\\reports\\actual.txt', '/workspace/b.csv'])

    def test_url_only_import_does_not_probe_share_or_rewrite_text(self):
        with tempfile.TemporaryDirectory() as root, patch.object(Path, 'is_file', side_effect=AssertionError('network stat')):
            text = '出处：https://example.org/financials.phtml'
            rewritten, imports = ingest_local_file_paths(text, root)
            self.assertEqual((rewritten, imports), (text, []))

    def test_unavailable_local_file_is_not_claimed_as_imported(self):
        with tempfile.TemporaryDirectory() as root, patch.object(Path, 'is_file', side_effect=OSError('unavailable')):
            text = '读取 C:\\unavailable\\a.txt'
            self.assertEqual(ingest_local_file_paths(text, root), (text, []))

    def test_existing_local_file_import_and_workspace_rewrite_still_work(self):
        with tempfile.TemporaryDirectory() as parent:
            root = Path(parent) / 'workspace'; root.mkdir()
            source = Path(parent) / 'report.txt'; source.write_text('public report', encoding='utf-8')
            text, imports = ingest_local_file_paths(str(source), root)
            self.assertEqual(len(imports), 1)
            self.assertEqual(text, '/workspace/data/report.txt')
            self.assertEqual((root/'data/report.txt').read_text(encoding='utf-8'), 'public report')
            rewritten, repeated = ingest_local_file_paths(str(root/'data/report.txt'), root)
            self.assertEqual((rewritten, repeated), ('/workspace/data/report.txt', []))

    def test_service_url_input_never_calls_path_stat(self):
        from bff.service import ChatSessionService
        with tempfile.TemporaryDirectory() as root:
            service = ChatSessionService.__new__(ChatSessionService)
            service._workspace_root = Path(root)
            service._snapshot = SimpleNamespace(virtual=lambda path: '/workspace/example')
            with patch.object(Path, 'is_file', side_effect=AssertionError('network stat')):
                text = '出处：https://example.org/source.txt'
                result = service.import_local_paths(text)
            self.assertEqual(result.rewritten_text, text)
            self.assertEqual(result.attachments, [])

    def test_service_stat_error_preserves_input(self):
        from bff.service import ChatSessionService
        with tempfile.TemporaryDirectory() as root:
            service = ChatSessionService.__new__(ChatSessionService)
            service._workspace_root = Path(root)
            service._snapshot = SimpleNamespace(virtual=lambda path: '/workspace/example')
            with patch.object(Path, 'is_file', side_effect=OSError('unavailable')):
                text = 'C:\\missing\\report.txt'
                result = service.import_local_paths(text)
            self.assertEqual(result.rewritten_text, text)
            self.assertEqual(result.attachments, [])
