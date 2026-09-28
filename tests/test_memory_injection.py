"""Memory injection must surface the newest entries, not the oldest."""
import unittest
from pathlib import Path

from core import config
from core.memory_manager import (
    _MAX_CONVERSATION_CHARS,
    _MAX_MEMORY_CHARS,
    _merge_shared_entry,
    _tail_window,
    _title_key,
)

CURRENT_INDEX = Path(config.WORKSPACE_PATH) / '.memory' / 'MEMORY.md'


class TailWindowTests(unittest.TestCase):
    def test_short_text_is_unchanged(self):
        self.assertEqual(_tail_window('abc', 10), 'abc')

    def test_overflow_keeps_the_newest_entries(self):
        text = '# Workspace Memory\n\n> header\n\n## [project] oldest\nOLD\n\n## [project] newest\nNEWEST-BODY'
        out = _tail_window(text, 60)
        self.assertLessEqual(len(out), 60)
        self.assertIn('NEWEST-BODY', out)
        self.assertNotIn('OLD', out)
        self.assertIn('已省略', out)

    def test_overflow_keeps_the_file_header_context(self):
        text = '# Workspace Memory\n\n> 本文件由 AI 助手自动维护。\n\n## [project] a\n' + 'x' * 500
        out = _tail_window(text, 120)
        self.assertTrue(out.startswith('# Workspace Memory'))
        self.assertIn('…(较早的记忆已省略', out)

    def test_real_memory_index_exposes_its_last_entry(self):
        if not CURRENT_INDEX.exists():
            self.skipTest('workspace memory index not present')
        raw = CURRENT_INDEX.read_text(encoding='utf-8')
        headings = [line for line in raw.splitlines() if line.startswith('## ')]
        if not headings:
            self.skipTest('memory index has no entries')
        out = _tail_window(raw, _MAX_MEMORY_CHARS)
        # The newest entry lives at the end of the file; it must survive.
        self.assertIn(headings[-1], out)
        if len(raw) > _MAX_MEMORY_CHARS:
            self.assertNotIn(headings[0], out)


class MergeTests(unittest.TestCase):
    BASE = '# Workspace Memory\n\n## [project] 语言偏好\n中文\n'

    def test_reworded_heading_replaces_instead_of_accumulating(self):
        existing = self.BASE + '\n## [project] 湖北正安项目风险审计\nOLD-BODY\n'
        entry = '## [project] 湖北正安项目风险审计要点\nNEW-BODY'
        merged = _merge_shared_entry(existing, entry)
        self.assertIn('NEW-BODY', merged)
        self.assertNotIn('OLD-BODY', merged)
        self.assertEqual(merged.count('风险审计'), 1)

    def test_exact_duplicate_is_a_no_op(self):
        entry = '## [project] 湖北正安项目风险审计\n同一正文'
        existing = self.BASE + f'\n{entry}\n'
        self.assertEqual(_merge_shared_entry(existing, entry), existing)

    def test_punctuation_only_variants_merge(self):
        existing = self.BASE + '\n## [project] 供应链月度汇报提炼方法\nOLD-BODY\n'
        merged = _merge_shared_entry(existing, '## [project] 供应链（月度）汇报-提炼方法\nNEW-BODY')
        self.assertIn('NEW-BODY', merged)
        self.assertNotIn('OLD-BODY', merged)
        self.assertEqual(merged.count('提炼方法'), 1)

    def test_short_headings_are_not_merged_on_a_coincidental_prefix(self):
        """Below the shared-prefix floor, distinct short topics must both stay."""
        existing = self.BASE + '\n## [project] 供应链月度汇报\nIDA-BODY\n'
        merged = _merge_shared_entry(existing, '## [project] 供应链数字化转型\nIDB-BODY')
        self.assertIn('IDA-BODY', merged)
        self.assertIn('IDB-BODY', merged)

    def test_genuinely_different_topics_both_survive(self):
        existing = self.BASE + '\n## [project] 供应链月度汇报提炼法\nIDA-BODY\n'
        merged = _merge_shared_entry(existing, '## [project] 供应链数字化转型：流程标准化策略\nIDB-BODY')
        self.assertIn('IDA-BODY', merged)
        self.assertIn('IDB-BODY', merged)
        self.assertEqual(merged.count('## [project]'), 3)

    def test_title_normalisation_ignores_punctuation_and_brackets(self):
        self.assertEqual(_title_key('供应链（月度）汇报-提炼法'), _title_key('供应链月度汇报提炼法'))

    def test_body_text_never_enters_the_title_key(self):
        """A whole entry must not be keyed: body words would corrupt matching."""
        self.assertEqual(_title_key('## [project] 主题甲\nBODY-WORDS-HERE'),
                         _title_key('## [project] 主题甲'))
        self.assertNotIn('BODY', _title_key('## [project] 主题甲\nBODY-WORDS-HERE'))

    def test_kind_tag_is_dropped_so_same_kind_topics_stay_distinct(self):
        self.assertEqual(_title_key('## [project] 湖北正安风险审计'),
                         _title_key('## [user] 湖北正安风险审计'))
        self.assertNotEqual(_title_key('## [project] 湖北正安风险审计'),
                            _title_key('## [project] 供应链数字化'))

    def test_whitespace_only_entry_is_ignored(self):
        existing = self.BASE
        self.assertEqual(_merge_shared_entry(existing, '   '), existing)


if __name__ == '__main__':
    unittest.main()
