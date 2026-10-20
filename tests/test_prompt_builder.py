from datetime import datetime, timedelta, timezone
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from core.prompt_builder import PromptBuilder


class PromptTimeContextTests(unittest.TestCase):
    def test_new_prompt_has_local_date_and_preserves_methods_and_permissions(self):
        now = datetime(2026, 1, 1, 0, 10, tzinfo=timezone(timedelta(hours=8)))
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            (path / '01_base.txt').write_text('基础规则不变', encoding='utf8')
            builder = PromptBuilder(modules_dir=path)
            with patch('core.prompt_builder.datetime') as clock:
                clock.now.return_value.astimezone.return_value = now
                text = builder.build(extra_sections=['工作区权限', '原方法'])
            self.assertTrue(text.startswith('本轮当前时间：2026-01-01T00:10:00+08:00'))
            for section in ('基础规则不变', '工作区权限', '原方法'):
                self.assertEqual(text.count(section), 1)
            self.assertEqual(builder.get_module('01_base'), '基础规则不变')

    def test_legacy_prompt_also_gets_time_without_rewriting_legacy_file(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'legacy.txt'
            path.write_text('原兼容提示', encoding='utf8')
            text = PromptBuilder(legacy_path=path).build()
            self.assertIn('本轮当前时间：', text)
            self.assertIn('当前时间不代表资料已更新', text)
            self.assertTrue(text.endswith('原兼容提示'))
            self.assertEqual(path.read_text(encoding='utf8'), '原兼容提示')
