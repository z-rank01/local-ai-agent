"""Offline tests for paged file_read: a file must be readable to its end."""
import os
import re
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'skills' / 'files'))

from file_ops import FileOps  # noqa: E402
from path_guard import PathGuard  # noqa: E402

from core import config  # noqa: E402
from core.tool_registry import ToolRegistry  # noqa: E402


_PAGE_MARKER_RE = re.compile(
    r'\n\[(?:本页为第 \d+-\d+ 字符，共 \d+ 字符；继续读取请传 offset=\d+|'
    r'已读到文件末尾：第 \d+-\d+ 字符，共 \d+ 字符)\]')


def _strip_markers(text):
    """Remove the paging markers, leaving only the file's own characters."""
    return _PAGE_MARKER_RE.sub('', text)


def _page_all(ops, path, page_size):
    """Read a file the way the model must: follow next_offset until it ends."""
    chunks, offset, pages = [], 0, 0
    while True:
        result = ops.read(path, offset=offset, max_chars=page_size)
        pages += 1
        chunks.append(result['content'])
        following = result.get('next_offset')
        if following is None:
            return ''.join(chunks), pages, result
        if following <= offset:
            raise AssertionError('next_offset must advance')
        offset = following
        if pages > 10000:
            raise AssertionError('paging did not terminate')


class PagingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.ops = FileOps(PathGuard(str(self.root)))

    def _write(self, name, text):
        """Write bytes verbatim so Windows newline translation cannot skew lengths."""
        path = self.root / name
        path.write_bytes(text.encode('utf-8'))
        return str(path)

    def test_small_file_is_single_page_and_complete(self):
        content = 'hello 世界\n'
        path = self._write('small.txt', content)
        result = self.ops.read(path)
        self.assertEqual(result['total_chars'], len(content))
        self.assertIsNone(result['next_offset'])
        self.assertTrue(result['content'].startswith(content))
        self.assertIn('已读到文件末尾', result['content'])
    def test_large_file_reads_to_the_end_by_paging(self):
        body = ''.join(f'line-{i:05d}\n' for i in range(4000))
        path = self._write('big.txt', body)
        first = self.ops.read(path, max_chars=1000)
        self.assertEqual(first['total_chars'], len(body))
        self.assertEqual(first['next_offset'], 1000)
        self.assertEqual(first['range'], {'start': 0, 'end': 1000, 'total': len(body)})
        self.assertIn('继续读取请传 offset=1000', first['content'])

        joined, pages, last = _page_all(self.ops, path, 1000)
        self.assertGreater(pages, 1)
        self.assertIsNone(last['next_offset'])
        # Page markers are the only added text; strip them and the original body
        # must be reproduced character for character.
        self.assertEqual(_strip_markers(joined), body)
        progress = _PAGE_MARKER_RE.findall(joined) or re.findall(
            r'已读到文件末尾：第 (\d+)-(\d+) 字符，共 (\d+) 字符', joined)
        self.assertTrue(progress)

    def test_latest_trading_days_are_reachable(self):
        """The tail of a daily-bar CSV must be readable, not silently lost."""
        rows = ['date,close'] + [f'2025-01-{i:02d},{100 + i}' for i in range(1, 29)]
        body = '\n'.join(rows) + '\n'
        path = self._write('daily.csv', body)
        # Page size far below the file length, as the old 8000-char cap was.
        joined, _, last = _page_all(self.ops, path, 120)
        self.assertIsNone(last['next_offset'])
        self.assertIn('2025-01-28,128', _strip_markers(joined))
        self.assertIn('date,close', _strip_markers(joined))

    def test_offset_at_or_past_end_reports_end_of_file(self):
        path = self._write('tiny.txt', 'abc')
        for offset in (3, 99):
            result = self.ops.read(path, offset=offset, max_chars=10)
            self.assertIn(f'[offset={offset} 已在文件末尾（共 3 字符）', result['content'])
            self.assertIsNone(result['next_offset'])
            self.assertEqual(result['total_chars'], 3)

    def test_sha256_still_describes_the_whole_file(self):
        path = self._write('hash.txt', 'x' * 5000)
        import hashlib
        expected = hashlib.sha256(('x' * 5000).encode()).hexdigest()
        page = self.ops.read(path, max_chars=100)
        self.assertEqual(page['sha256'], expected)

    def test_excel_and_pdf_paths_page_over_extracted_text(self):
        # Conversion happens before windowing, so the window never splits bytes.
        text = 'sheet,value\n' + ''.join(f'r{i},{i}\n' for i in range(500))
        result = FileOps._window(text, '/workspace/x.xlsx', 'abc', 0, 100, {})
        self.assertEqual(result['total_chars'], len(text))
        self.assertEqual(result['next_offset'], 100)
        self.assertEqual(result['content'][:11], 'sheet,value')
        self.assertNotIn('\ufffd', result['content'])

    def test_unsupported_binary_keeps_metadata_contract(self):
        path = self.root / 'blob.bin'
        path.write_bytes(b'\x00\x01\x02' * 500)
        result = self.ops.read(str(path))
        self.assertTrue(result['unsupported'])
        self.assertEqual(result['extension'], '.bin')
        self.assertIn('file_convert', result['hint'])
    def test_zero_max_chars_falls_back_to_server_limit(self):
        path = self._write('z.txt', 'abcdef')
        self.assertEqual(self.ops.read(path, max_chars=0)['content'][:6], 'abcdef')


class ToolLimitTests(unittest.TestCase):
    """The paging window must survive the router's own truncation."""

    def setUp(self):
        # web_fetch only registers when websearch is enabled.
        self.registry = ToolRegistry(config.TOOLS_DIR, enable_websearch=True)

    def test_file_read_limit_exceeds_a_real_data_file(self):
        limit = self.registry.get_max_result_chars('file_read')
        csv = Path(config.WORKSPACE_PATH) / 'data' / '600519_daily.csv'
        self.assertGreater(limit, 8000)
        self.assertIn('file_read', self.registry.known_tools)
        if csv.exists():
            self.assertGreater(limit, csv.stat().st_size,
                               'a real workspace CSV must fit in one page')

    def test_limits_are_declared_per_tool_not_shared(self):
        expected = {'file_read': 200000, 'web_fetch': 50000, 'code_exec': 32000,
                    'shell_exec': 16000, 'file_list': 16000, 'file_convert': 200000}
        for name, value in expected.items():
            self.assertEqual(self.registry.get_max_result_chars(name), value, name)

    def test_web_fetch_advertised_max_is_now_honoured(self):
        # web_fetch.yaml tells the model "max 50000"; the engine must not cap lower.
        definitions = {d['function']['name']: d for d in self.registry.get_definitions()}
        self.assertIn('50000', definitions['web_fetch']['function']['parameters']
                      ['properties']['max_chars']['description'])
        self.assertGreaterEqual(self.registry.get_max_result_chars('web_fetch'), 50000)

    def test_read_window_env_override(self):
        import importlib
        import file_ops as module
        previous = os.environ.get('FILE_READ_MAX_CHARS')
        os.environ['FILE_READ_MAX_CHARS'] = '1234'
        try:
            importlib.reload(module)
            self.assertEqual(module._read_char_limit(), 1234)
        finally:
            if previous is None:
                os.environ.pop('FILE_READ_MAX_CHARS', None)
            else:
                os.environ['FILE_READ_MAX_CHARS'] = previous
            importlib.reload(module)


if __name__ == '__main__':
    unittest.main()
