"""Deterministic read-only comparison for explicitly requested brief versions."""

from __future__ import annotations

import re
from typing import Any


_DATE = re.compile(r'\d{4}-\d{2}-\d{2}')


def requested(query: str) -> bool:
    """Limit this renderer to the explicit no-refresh, paged version request."""
    return ('简报' in query and '对比' in query and '页' in query
            and any(word in query for word in ('旧版', '旧版本', '上一版'))
            and any(word in query for word in ('不刷新', '不要刷新', '无需刷新'))
            and not any(word in query for word in ('事件', '正文', '公司', '影响', '逐条', '原因'))
            and not _DATE.search(query)
            and not re.search(r'\d{1,2}月\d{1,2}日', query))


class BriefComparison:
    def __init__(self) -> None:
        self.versions: list[dict[str, Any]] = []
        self.reads: dict[str, dict[str, Any]] = {}

    def accept(self, tool: dict[str, Any]) -> None:
        name = tool.get('name')
        result = tool.get('result') or {}
        observation = result.get('model_observation') if isinstance(result, dict) else None
        if not isinstance(observation, dict):
            return
        if name == 'stock_market_brief_find' and observation.get('kind') == 'task_search':
            self.versions = [item for item in (observation.get('items') or [])
                             if isinstance(item, dict) and isinstance(item.get('reference'), str)]
        if name != 'stock_report_read':
            return
        params = tool.get('params') or {}
        reference = params.get('reference')
        if not isinstance(reference, str):
            return
        page = params.get('page', 1)
        if type(page) is not int or page < 1:
            return
        entry = self.reads.setdefault(reference, {'pages': set(), 'attempted': set(), 'observation': None})
        entry['attempted'].add(page)
        if result.get('local_result_available'):
            entry['pages'].add(page)
        if page == 1 and observation.get('kind') == 'market_brief':
            entry['observation'] = observation

    def missing_required_reads(self) -> list[tuple[str, int]]:
        """Open the latest two versions, including page two of the latest."""
        if len(self.versions) < 2:
            return []
        latest, previous = (item['reference'] for item in self.versions[:2])
        required = [(latest, 1), (latest, 2), (previous, 1)]
        return [(reference, page) for reference, page in required
                if page not in self.reads.get(reference, {}).get('attempted', set())]

    def render(self) -> str:
        if len(self.versions) < 2:
            return ('尚未核对到两份可读取的市场简报，因此不能可靠对比新旧版。'
                    '已打开的报告页仍可在本地附件查看。')
        new, old = self.versions[:2]
        new_read = self.reads.get(new['reference'])
        old_read = self.reads.get(old['reference'])
        if not new_read or not old_read or not new_read['observation'] or not old_read['observation']:
            return ('最新版或紧邻前一旧版的报告未能读取，不能可靠对比。'
                    '已打开的报告页仍可在本地附件查看。')
        new_info = self._info(new, new_read)
        old_info = self._info(old, old_read)
        rows = [
            ('版本创建时间', 'created'),
            ('行情对应交易日', 'trade_day'),
            ('资讯核对区间', 'news_window'),
            ('资讯核对截止日', 'news_end'),
            ('东方财富公告目录失败日期', 'failed_dates'),
            ('事件分析情况', 'analysis'),
        ]
        lines = ['## 市场简报新旧版对比', '', '| 核对项 | 最新版 | 前一旧版 |',
                 '| --- | --- | --- |']
        for label, key in rows:
            lines.append(f'| {label} | {new_info[key]} | {old_info[key]} |')
        opened = []
        for label, read in (('最新版', new_read), ('旧版', old_read)):
            pages = sorted(read['pages'])
            if pages:
                opened.append(label + '第 ' + '、'.join(str(page) for page in pages) + ' 页')
        lines += ['', '已为你打开本地报告附件：' + '；'.join(opened) + '。',
                  '以上是版本元信息对比。报告页内事件尚未逐条核对；'
                  '新版未记录失败日期，不证明旧版失败已补采。'
                  '两版均为部分完成，也不能推断缺口内容相同。']
        if 2 not in new_read['pages']:
            lines.append('最新版第 2 页未能打开，页内内容仍待查看。')
        return '\n'.join(lines)

    @staticmethod
    def _info(version: dict[str, Any], read: dict[str, Any]) -> dict[str, str]:
        observation = read['observation']
        created = version.get('created_at')
        created_text = created.replace('T', ' ')[:19] if isinstance(created, str) else '未核对'
        trade = _DATE.findall(str(observation.get('trading_day_description') or ''))
        news = _DATE.findall(str(observation.get('news_window_description') or ''))
        failures = str(observation.get('announcement_directory_coverage') or '')
        failed_dates = _DATE.findall(failures)
        analysis = str(observation.get('analysis_status_description') or '')
        if '部分完成' in analysis:
            analysis_label = '部分完成，仍有缺口'
        elif '已完成' in analysis:
            analysis_label = '已完成'
        elif '待核查' in analysis:
            analysis_label = '待核查'
        else:
            analysis_label = '未核对'
        return {
            'created': created_text,
            'trade_day': trade[0] if trade else '未核对',
            'news_window': f'{news[0]} 至 {news[1]}' if len(news) >= 2 else '未核对',
            'news_end': news[-1] if news else '未核对',
            'failed_dates': '、'.join(failed_dates) if failed_dates else ('未记录' if '未记录' in failures else '未核对'),
            'analysis': analysis_label,
        }
