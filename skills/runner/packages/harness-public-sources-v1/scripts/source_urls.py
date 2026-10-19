"""Build public provider entry URLs. No networking, facts, or security-name inference."""
import argparse
from datetime import date
import json
import re
from urllib.parse import urlencode


def source_url(kind, *, symbol=None, start=None, end=None, article=None, page=1):
    if page < 1:
        raise ValueError('page must be positive')
    if kind == 'notice':
        if not isinstance(article, str) or not re.fullmatch(r'AN\d+', article):
            raise ValueError('article must be the actual AN identifier returned by the catalogue')
        return 'https://np-cnotice-stock.eastmoney.com/api/content/ann?' + urlencode({
            'art_code': article, 'client_source': 'web', 'page_index': page, 'cb': 'callback'})
    if not isinstance(symbol, str) or not re.fullmatch(r'(SH|SZ|BJ)\d{6}', symbol):
        raise ValueError('use a verified exchange-qualified symbol, for example SH688178')
    if kind == 'finance':
        return 'https://emweb.securities.eastmoney.com/PC_HSF10/NewFinanceAnalysis/ZYZBAjaxNew?' + urlencode({'type': 0, 'code': symbol})
    if kind == 'profile':
        return 'https://emweb.securities.eastmoney.com/PC_HSF10/CompanySurvey/CompanySurveyAjax?' + urlencode({'code': symbol})
    if kind != 'notices':
        raise ValueError('unknown source kind')
    if not start or not end or date.fromisoformat(start) > date.fromisoformat(end):
        raise ValueError('notices requires an explicit valid start/end date interval')
    return 'https://np-anotice-stock.eastmoney.com/api/security/ann?' + urlencode({
        'sr': -1, 'page_size': 100, 'page_index': page, 'ann_type': 'A', 'client_source': 'web',
        'f_node': 0, 's_node': 0, 'stock_list': symbol[2:], 'begin_time': start, 'end_time': end})


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('kind', choices=('notices', 'notice', 'finance', 'profile'))
    parser.add_argument('--symbol')
    parser.add_argument('--start')
    parser.add_argument('--end')
    parser.add_argument('--article')
    parser.add_argument('--page', type=int, default=1)
    args = vars(parser.parse_args())
    print(json.dumps({'url': source_url(**args)}, ensure_ascii=False))
