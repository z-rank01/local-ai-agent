"""Deterministic publication checks and accessible SVG charts."""
from decimal import Decimal, localcontext
from html import escape
import re


def number(value):
    text = str(value)
    if re.fullmatch(r'[+-]?\d{1,3}(?:,\d{3})+(?:\.\d+)?', text):
        text = text.replace(',', '')
    result = Decimal(text)
    if not result.is_finite():
        raise ValueError('数值必须有限')
    return result


def quoted_number(value, quote):
    clean = quote.replace(',', '').replace('，', '')
    return any(number(match.group()) == number(value) for match in
               re.finditer(r'(?<![\d.])-?\d+(?:\.\d+)?(?![\d.])', clean))


def validate_publication(params, sources, read_text):
    for key in ('title', 'markdown', 'as_of', 'stance'):
        if not isinstance(params.get(key), str) or not params[key].strip():
            raise ValueError(f'发布缺少 {key}')
    if not params.get('review', {}).get('checks') or not params['review'].get('limitations'):
        raise ValueError('须提供已做的核对和资料限制')
    cited = {}
    for item in params['sources']:
        key, quote = item['id'], item['quote']
        if key not in sources or not quote.strip() or not item['locator']:
            raise ValueError('引用须绑定本轮实际来源及原文位置')
        if not any(quote in text for text in read_text.get(key, [])):
            raise ValueError(f'{key} 引文不在实际收到的原文中')
        cited.setdefault(key, {'quote': ''})['quote'] += '\n' + quote
    if not cited:
        raise ValueError('简报没有实际原文引用')
    calculations = params.get('calculations', [])
    for index, calc in enumerate(calculations):
        values = [number(row['value']) for row in calc['inputs']]
        if not values or any(not row['period'] or not row['unit'] for row in calc['inputs']):
            raise ValueError('计算输入缺少来源、期间或单位')
        for input_index, row in enumerate(calc['inputs']):
            if row['source'].startswith('calculation:'):
                reference = row['source'].removeprefix('calculation:')
                if not reference.isdecimal() or not 0 <= int(reference) < index:
                    raise ValueError('计算依赖只能引用此前已核对的 calculation:零起始索引')
                previous = calculations[int(reference)]
                if number(row['value']) != number(previous['result']) or row['unit'] != previous['unit']:
                    raise ValueError('计算依赖的原值或单位与前项结果不一致')
                continue
            if row['source'].startswith('constant:') and row['period'] == '不适用':
                continue
            if row['source'] not in cited or not quoted_number(row['value'], cited[row['source']]['quote']):
                raise ValueError(f"calculations[{index}].inputs[{input_index}] 的 {row['value']} 未在来源 {row['source']} 引句中定位；换算或中间结果须引用前项计算，数学常数须标明含义")
        with localcontext() as ctx:
            ctx.prec = 40
            operation = calc['operation']
            if operation in ('sum', 'subtract') and len({r['unit'] for r in calc['inputs']}) != 1:
                raise ValueError('加减单位不一致')
            if operation == 'sum':
                value = sum(values, Decimal(0))
            elif len(values) == 2 and operation in ('subtract', 'multiply', 'divide'):
                a, b = values
                value = a-b if operation == 'subtract' else a*b if operation == 'multiply' else a/b
            else:
                raise ValueError('不支持的算术操作')
            if value != number(calc['result']):
                raise ValueError('计算结果不一致，请用容器计算后保留完整精度')
    for chart_index, chart in enumerate(params.get('charts', [])):
        if not all(chart.get(k) for k in ('title', 'unit', 'period', 'items')):
            raise ValueError('图表须有标题、单位、期间与数据')
        for item_index, item in enumerate(chart['items']):
            value = number(item['value'])
            if 'calculation' in item:
                index = item['calculation']
                if type(index) is not int or not 0 <= index < len(calculations):
                    raise ValueError('图表计算索引无效')
                if value != number(calculations[index]['result']) or chart['unit'] != calculations[index]['unit']:
                    raise ValueError('图表值或单位与计算不一致')
            else:
                source = cited.get(item.get('source'))
                if source is None or not quoted_number(item['value'], source['quote']):
                    raise ValueError('图表原值须在对应原文引句中；单位转换请使用计算结果')
                unit = re.escape(chart['unit'])
                if not re.search(r'(?:\d\s*|单位\s*[:：]\s*|[（(]\s*)' + unit + r'(?![\w])', source['quote']):
                    raise ValueError(f"charts[{chart_index}].items[{item_index}] 的原文未明确匹配单位 {chart['unit']}；请引用单位说明或保留换算计算")


def render_charts(charts):
    figures = []
    for chart in charts:
        items = chart['items']
        if len(items) > 12:
            raise ValueError('单图最多12项，请拆图避免拥挤')
        values = [number(row['value']) for row in items]
        scale = max([abs(v) for v in values] + [Decimal(1)])
        svg = f'<svg viewBox="0 0 620 {len(items)*65+25}" role="img" aria-label="{escape(chart["title"], quote=True)}">'
        for i, (row, value) in enumerate(zip(items, values)):
            y, width = 26 + i * 65, float(abs(value)/scale*250)
            x = 310-width if value < 0 else 310
            svg += (f'<text x="8" y="{y}" font-size="15">{escape(row["label"])}</text>'
                f'<rect x="{x:.2f}" y="{y+8}" width="{width:.2f}" height="16" fill="#8f6a1d"/>'
                f'<line x1="310" x2="310" y1="{y+4}" y2="{y+29}" stroke="#666"/>'
                f'<text x="610" y="{y}" text-anchor="end" font-size="15">{value:,.2f}</text>')
        svg += '</svg>'
        figures.append(f'<figure><figcaption>{escape(chart["title"])}</figcaption><p class="unit">{escape(chart["period"])} · {escape(chart["unit"])} · 零起点，负值向左；来源见文末</p><div class="chart-panel">{svg}</div></figure>')
    return '<section><h2>关键图示</h2>'+''.join(figures)+'</section>' if figures else ''
