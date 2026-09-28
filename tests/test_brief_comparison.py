import unittest

from core.brief_comparison import BriefComparison, requested


class BriefComparisonTests(unittest.TestCase):
    def test_only_explicit_read_only_paged_comparison_uses_verified_renderer(self):
        self.assertTrue(requested('获取当前市场简报，不刷新，并打开最新报告第 1、2 页与旧版对比。'))
        self.assertFalse(requested('刷新市场简报并分析事件差异'))
        self.assertFalse(requested('获取当前市场简报，不刷新'))
        self.assertFalse(requested('市场简报不刷新，打开第 1 页与 2026-09-27 的旧版对比'))
        self.assertFalse(requested('市场简报不刷新，打开第 1 页与旧版逐条对比事件影响'))

    def test_renders_only_verified_version_metadata_and_opened_pages(self):
        comparison = BriefComparison()
        latest, older = 'a' * 64, 'b' * 64
        comparison.accept({'name': 'stock_market_brief_find', 'result': {'model_observation': {
            'kind': 'task_search', 'items': [
                {'reference': latest, 'created_at': '2026-09-28T00:00:07+08:00'},
                {'reference': older, 'created_at': '2026-09-27T22:50:45+08:00'},
            ]}}})
        self.assertEqual(comparison.missing_required_reads(), [(latest, 1), (latest, 2), (older, 1)])
        for reference, page, news_end, failures in (
            (latest, 1, '2026-09-28', '未记录'),
            (latest, 2, '2026-09-28', '未记录'),
            (older, 1, '2026-09-27', '2026-09-24'),
        ):
            observation = {'kind': 'market_brief' if page == 1 else 'market_brief_page',
                           'trading_day_description': '行情锚定的最近交易日：2026-09-24。',
                           'news_window_description': f'资讯核对区间：2026-09-24 至 {news_end}；资讯核对截止日：{news_end}。',
                           'announcement_directory_coverage': f'东方财富公告目录失败日期：{failures}。',
                           'analysis_status_description': '本次事件分析部分完成，仍有缺口'}
            comparison.accept({'name': 'stock_report_read', 'params': {'reference': reference, 'page': page},
                               'result': {'model_observation': observation, 'local_result_available': True}})
        rendered = comparison.render()
        self.assertEqual(comparison.missing_required_reads(), [])
        self.assertIn('2026-09-28', rendered)
        self.assertIn('2026-09-27', rendered)
        self.assertIn('最新版第 1、2 页；旧版第 1 页', rendered)
        self.assertIn('不证明旧版失败已补采', rendered)
        self.assertNotIn(latest, rendered)
        self.assertNotIn(older, rendered)
        self.assertNotIn('stock_', rendered)
        self.assertNotIn('SUCCEEDED', rendered)

    def test_does_not_label_an_arbitrary_older_read_as_previous_version(self):
        comparison = BriefComparison()
        latest, previous, distant = 'a' * 64, 'b' * 64, 'c' * 64
        comparison.accept({'name': 'stock_market_brief_find', 'result': {'model_observation': {
            'kind': 'task_search', 'items': [
                {'reference': latest, 'created_at': '2026-09-28T00:00:07+08:00'},
                {'reference': previous, 'created_at': '2026-09-27T22:50:45+08:00'},
                {'reference': distant, 'created_at': '2026-09-27T00:39:42+08:00'},
            ]}}})
        observation = {'kind': 'market_brief', 'trading_day_description': '最近交易日：2026-09-24。'}
        for reference in (latest, distant):
            comparison.accept({'name': 'stock_report_read', 'params': {'reference': reference, 'page': 1},
                               'result': {'model_observation': observation, 'local_result_available': True}})
        self.assertIn((previous, 1), comparison.missing_required_reads())
        self.assertIn('不能可靠对比', comparison.render())


if __name__ == '__main__':
    unittest.main()
