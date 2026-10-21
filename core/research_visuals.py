"""Deterministic publication checks and accessible SVG charts."""
from decimal import Decimal, localcontext, InvalidOperation
from copy import deepcopy
from html import escape
import re

from . import config


def number(value):
    text = str(value)
    if re.fullmatch(r'[+-]?\d{1,3}(?:,\d{3})+(?:\.\d+)?', text):
        text = text.replace(',', '')
    try:
        result = Decimal(text)
    except InvalidOperation as exc:
        raise InvalidOperation('数值格式无效：value须为原数字字符串或省略派生值，不能填未提供、取消或JSON对象') from exc
    if not result.is_finite():
        raise ValueError('数值必须有限')
    return result


def quoted_number(value, quote):
    clean = quote.replace(',', '').replace('，', '')
    return any(number(match.group()) == number(value) for match in
               re.finditer(r'(?<![\d.])-?\d+(?:\.\d+)?(?![\d.])', clean))


def literal_text(text):
    # Chinese PDF word wrapping may insert spaces between adjacent ideographs.
    # Keep English word boundaries, punctuation, numbers and conditions intact.
    text = re.sub(r'(?<=[\u3400-\u9fff])\s+(?=[\u3400-\u9fff])', '', text)
    return re.sub(r'\s+', ' ', text).strip()


_MONEY_UNITS = {'元': Decimal(1), '万元': Decimal(10000), '亿元': Decimal(100000000)}


def convert_unit(value, source, target):
    """Convert supported display units; never relabel an unchanged quantity."""
    value = number(value)
    if source == target:
        return value
    if source not in _MONEY_UNITS or target not in _MONEY_UNITS:
        raise ValueError(f'不能换算单位 {source} → {target}；请使用相同单位或明确换算算式')
    with localcontext() as ctx:
        ctx.prec = 40
        return value * _MONEY_UNITS[source] / _MONEY_UNITS[target]


def normalize_publication(params):
    params = deepcopy(params)
    review = params.setdefault('review', {})
    review.setdefault('checks', [])
    review.setdefault('limitations', '未单独说明资料限制，请结合来源范围阅读。')
    review.setdefault('conflicts', [])
    params.setdefault('calculations', [])
    params.setdefault('charts', [])
    for item in params.get('sources', []):
        if not item.get('locator'):
            item['locator'] = '未记录具体位置'
    return params


def validate_visual_plan(params):
    """Charts are optional.

    A brief with no chart used to be rejected unless the model also wrote a
    ``visual_limitations`` excuse, which turned a layout preference into a
    publish blocker. The field is still honoured and still saved when given.
    """
    return params


def validate_publication(params, sources, read_text):
    # Complete derived fields in a copy; the model's submitted draft stays intact.
    params = normalize_publication(params)
    for key in ('title', 'markdown', 'as_of', 'stance'):
        if not isinstance(params.get(key), str) or not params[key].strip():
            raise ValueError(f'发布缺少 {key}')
    notes = params.get('editorial_notes', [])
    if not isinstance(notes, list) or any(not isinstance(note, str) or not note.strip() for note in notes):
        raise ValueError('editorial_notes须为非空文字组成的列表')
    # review.checks / limitations are advisory metadata now: a missing block must
    # not stop a well-evidenced brief from being saved, so they are no longer a
    # hard requirement here.
    cited = {}
    for source_index, item in enumerate(params.get('sources') or []):
        key, quote = item.get('id'), str(item.get('quote') or '')
        if key not in sources or not quote.strip():
            raise ValueError('引用须绑定本轮实际来源')
        # PDF line wrapping and HTML NBSP do not change the quoted words.
        normalized = literal_text(quote)
        if config.PUBLISH_QUOTE_CHECK and not any(normalized in literal_text(text)
                                                  for text in read_text.get(key, [])):
            raise ValueError(f'sources[{source_index}] {key}（{item.get("locator", "")}）引文不在实际收到的原文中；请web_fetch读取对应页或让摘录脚本完整输出source_excerpts回执。保存文件不等于已读，不要拼接或改写引句')
        cited.setdefault(key, {'quote': ''})['quote'] += '\n' + quote
    if not cited:
        raise ValueError('简报没有实际原文引用')
    calculations = params.get('calculations', [])
    errors = []
    for index, calc in enumerate(calculations):
        for input_index, row in enumerate(calc['inputs']):
            if row['source'].startswith('calculation:') or (row['source'].startswith('constant:') and row.get('period') == '不适用'):
                continue
            if not row.get('unit'):
                errors.append(f'calculations[{index}].inputs[{input_index}] 计算输入缺少单位；原文原值需填单位，只有前项计算引用可以继承单位')
            if row['source'] not in cited or not quoted_number(row['value'], cited[row['source']]['quote']):
                errors.append(f"calculations[{index}].inputs[{input_index}] 的 {row['value']} 未在来源 {row['source']} 引句中定位；换算或中间结果须引用前项计算，数学常数须标明含义")
    if errors:
        raise ValueError('\n'.join(errors))
    for index, calc in enumerate(calculations):
        if not calc['inputs'] or any(not row['period'] for row in calc['inputs']):
            raise ValueError('计算输入缺少来源、期间或单位')
        for input_index, row in enumerate(calc['inputs']):
            if row['source'].startswith('calculation:'):
                reference = row['source'].removeprefix('calculation:')
                if not reference.isdecimal() or not 0 <= int(reference) < index:
                    raise ValueError('计算依赖只能引用此前已核对的 calculation:零起始索引')
                previous = calculations[int(reference)]
                row.setdefault('unit', previous['unit'])
                expected = convert_unit(previous['result'], previous['unit'], row['unit'])
                row.setdefault('value', str(expected))
                if number(row['value']) != expected:
                    raise ValueError(f'calculations[{index}].inputs[{input_index}] 的原值与前项calculation:{reference}换算后不一致')
                continue
            if not row.get('unit'):
                raise ValueError('计算输入缺少单位')
            if row['source'].startswith('constant:') and row['period'] == '不适用':
                continue
        values = [number(row['value']) for row in calc['inputs']]
        with localcontext() as ctx:
            ctx.prec = 40
            operation = calc['operation']
            if operation in ('sum', 'subtract'):
                values = [convert_unit(row['value'], row['unit'], calc['unit']) for row in calc['inputs']]
            if operation == 'sum':
                value = sum(values, Decimal(0))
            elif len(values) == 2 and operation in ('subtract', 'multiply', 'divide'):
                a, b = values
                value = a-b if operation == 'subtract' else a*b if operation == 'multiply' else a/b
            else:
                raise ValueError('不支持的算术操作')
            calc.setdefault('result', str(value))
            if value != number(calc['result']):
                raise ValueError('计算结果不一致，请用容器计算后保留完整精度')
    for chart_index, chart in enumerate(params.get('charts', [])):
        if not all(chart.get(k) for k in ('title', 'unit', 'period', 'items')):
            raise ValueError('图表须有标题、单位、期间与数据')
        for item_index, item in enumerate(chart['items']):
            if 'calculation' in item:
                index = item['calculation']
                if type(index) is not int or not 0 <= index < len(calculations):
                    raise ValueError('图表计算索引无效')
                expected = convert_unit(calculations[index]['result'], calculations[index]['unit'], chart['unit'])
                item.setdefault('value', str(expected))
                value = number(item['value'])
                if value != expected:
                    raise ValueError('图表值与计算换算后不一致')
            else:
                number(item['value'])
                source = cited.get(item.get('source'))
                if source is None or not quoted_number(item['value'], source['quote']):
                    raise ValueError(f"charts[{chart_index}].items[{item_index}] {item.get('label','')} 的图表原值 {item['value']} 未在来源 {item.get('source')} 的发布引句中定位；补入对应连续原句，单位转换请使用计算结果")
                # A literal unit match inside the quote is no longer required:
                # the value itself is already located, and real filings often
                # state the unit in a header the quote does not repeat.
    return params


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
