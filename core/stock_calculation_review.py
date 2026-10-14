"""Read-only consistency checks for typed public program scale receipts.

No formula execution, natural-language intent routing or research submission.
Only a matched explicit division can be repaired. Other reasoning remains a
model judgement and is not certified by this narrow arithmetic check.
"""
from __future__ import annotations

import json
import re
from decimal import Decimal, InvalidOperation, localcontext
from urllib.parse import urlparse
import ipaddress
from copy import deepcopy

_NUM = r'(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?'
_DIV = re.compile(r'(?<![\w,.])(?P<n>'+_NUM+r')\s*(?P<nu>亿元|万元|元)\s*[/÷]\s*'
                  r'(?P<d>'+_NUM+r')\s*(?P<du>亿元|万元|元)\s*(?:≈|=|约为|约等于)\s*[*_]*\s*'
                  r'(?P<result>'+_NUM+r')\s*[*_]*\s*(?P<unit>倍|%|％)')
_FACTORS = {'元': Decimal(1), '万元': Decimal(10000), '亿元': Decimal(100000000)}


def validate_calculations(value):
    """Validate a backend calculation receipt, not primary source authority."""
    if not isinstance(value, list):
        raise ValueError('程序计算回执必须为列表')
    for row in value:
        if (not isinstance(row, dict) or row.get('kind') != 'EVENT_SCALE'
                or row.get('version') != 'event-scale-calculation-v1'
                or not re.fullmatch(r'\d{6}', str(row.get('symbol', '')))
                or not re.fullmatch(r'[12]\d{3}1231', str(row.get('annual_period', '')))
                or not re.fullmatch(r'[0-9a-f]{64}', str(row.get('reader_revision', '')))):
            raise ValueError('程序计算类型、期间或展示版本无效')
        try:
            with localcontext() as ctx:
                ctx.prec = 28
                for key in ('amount_yuan', 'annual_revenue_yuan', 'ratio', 'percent'):
                    if not isinstance(row.get(key), str) or not re.fullmatch(r'\d+(?:\.\d+)?', row[key]):
                        raise ValueError('程序数值格式无效')
                n, d, ratio, percent = (Decimal(row[k]) for k in ('amount_yuan', 'annual_revenue_yuan', 'ratio', 'percent'))
                if n <= 0 or d <= 0 or ratio != n / d or percent != ratio * 100:
                    raise ValueError('程序数值与比值不一致')
        except (InvalidOperation, ZeroDivisionError):
            raise ValueError('程序数值不适用') from None
    return value


def calculation_index(messages, *, receipts=None):
    """Latest successful paired report only, never user/model-stage content."""
    from .stock_receipts import paired_receipts
    latest = {}
    for receipt in (paired_receipts(messages, {'stock_report_read'}, allow_legacy_reference=False) if receipts is None else receipts):
        if receipt.tool_name != 'stock_report_read' or receipt.observation.get('source_reference') != receipt.reference:
            continue
        value, ref = receipt.observation, receipt.reference
        if value['kind'] != 'local_report_page':
            continue
        try:
            # New versions without a valid computation do not inherit one.
            latest[ref] = []
            rows = validate_calculations(value.get('program_calculations', []))
            pages = value.get('answer_pages')
            if rows and (not isinstance(pages, list) or not pages
                    or not all(isinstance(page, dict) and page.get('reader_revision') == item['reader_revision']
                               for page in pages for item in rows)):
                continue
            safe=[]
            for item in rows:
                row={**item,'source_reference':ref}
                try:
                    validated_annual_source(row)
                except ValueError:
                    row.pop('annual_source',None)
                if 'service_mean' in row:
                    from .stock_calculation_contract import validated_service_mean
                    try:
                        validated_service_mean(row, ''.join(page['body'] for page in pages))
                    except (ValueError, TypeError, KeyError, ArithmeticError):
                        row.pop('service_mean', None)
                safe.append(row)
            latest[ref] = safe
        except (TypeError, ValueError, AttributeError):
            continue
    return latest


def calculation_records(messages):
    return [item for rows in calculation_index(messages).values() for item in rows]


def validated_annual_source(row):
    source=row.get('annual_source')
    if source is None:return None
    if (not isinstance(source,dict) or source.get('kind')!='SOURCE_FIELD'
            or source.get('metric')!='revenue' or source.get('unit')!='CNY'
            or source.get('symbol')!=row['symbol'] or source.get('report_period')!=row['annual_period']
            or source.get('value_yuan')!=row['annual_revenue_yuan']
            or not isinstance(source.get('location'),str) or not source['location'].strip()
            or not re.fullmatch('[0-9a-f]{16}',str(source.get('event_id','')))
            or not re.fullmatch(r'\d+:[0-9a-f]{16}',str(source.get('read_cursor','')))):
        raise ValueError('年度来源字段与程序基数不一致')
    url=urlparse(str(source.get('url','')));host=url.hostname or ''
    if url.scheme not in ('http','https') or not host or url.username or url.password or host=='localhost' or host.endswith('.local'):
        raise ValueError('年度来源地址无效')
    try:
        if not ipaddress.ip_address(host).is_global:raise ValueError('年度来源地址非公开')
    except ValueError as exc:
        if host.replace('.','').isdigit() or ':' in host:raise ValueError('年度来源地址非公开') from exc
    return source


def current_program_records(index,user_request,source_index):
    refs=set(re.findall(r'(?<![0-9a-f])[0-9a-f]{64}(?![0-9a-f])',user_request))
    if not refs and source_index:refs={source_index[0]['source_reference']}
    return [row for ref,rows in index.items() if ref in refs for row in rows]


def program_source_guidance(records):
    if not records:return None
    from .stock_financial_receipts import currency_display
    view=deepcopy(records)
    for row in view:
        row['unit_display']={'项目总额':currency_display(row['amount_yuan']),
                             '年度营业收入':currency_display(row['annual_revenue_yuan'])}
    return ('[当前任务已授权程序规模参照及基数来源索引，非模型阶段推断，不向用户展示内部编号。'
            '金额、年度和比值是同版程序结果；年度字段若提供SOURCE_FIELD，属于已保存的结构化财务来源，'
            '不是半年年化、模型估计或逐字年报全文。用户要求年度参照时采用该年度基数，'
            '不能因其他原文窗口没有该字段而换成半年年化，或声称年度资料不存在。'
            '需要核对字段时，annual_source.event_id/read_cursor与外层source_reference配套调用现有读取工具，'
            '不能替换引用或编造游标。需要年报原句则另核对真实原文，字段不充当逐字引文。'
            '比例仅作规模参照，签约、利润、回款、收入确认年份和完成度须另有依据。]\n'
            +'unit_display只将同一已验证人民币数值转换为展示单位并保留精度；可四舍五入并写约，'
            '不能截掉小数后仍标成精确值。复用已有授权回执不代表本轮重新读取或更新资料，'
            '仅实际新工具成功才可声称本轮重新核对。\n'
            +json.dumps(view,ensure_ascii=False))


def _source_matches(text, unit, source):
    value = Decimal(text.replace(',', ''))
    ground = Decimal(source)
    displayed = value * _FACTORS[unit]
    # Permit presentation rounding such as 50,076.41万元, without accepting a
    # changed denominator (e.g. half-year revenue or annualised revenue).
    half_step = Decimal(10) ** value.as_tuple().exponent * _FACTORS[unit] / 2
    return abs(displayed - ground) <= half_step and abs(displayed - ground) <= abs(ground) * Decimal('0.000001')


def repair_scale_divisions(reply, records):
    """Correct a source-matched division and isolate its dependent false scale.

    Unsupported expressions and unrelated figures are left unchanged. This
    does not approve the report, its sources or its business conclusions.
    """
    paragraphs = re.split(r'\n\s*\n', reply)
    edits, bad_scales = [], []
    for position, paragraph in enumerate(paragraphs):
        def repair(match):
            candidates = [row for row in records
                          if _source_matches(match['n'], match['nu'], row['amount_yuan'])
                          and _source_matches(match['d'], match['du'], row['annual_revenue_yuan'])]
            unique = {(r['ratio'], r['annual_period']) for r in candidates}
            if len(unique) != 1:
                return match[0]
            row = candidates[0]
            expected = Decimal(row['ratio']) if match['unit'] == '倍' else Decimal(row['percent'])
            claimed = Decimal(match['result'].replace(',', ''))
            epsilon = Decimal(10) ** claimed.as_tuple().exponent / 2
            if claimed > 0 and abs(expected - claimed) <= epsilon:
                return match[0]
            exact = format(expected.quantize(Decimal('0.01')), 'f')
            start, end = match.span('result')
            local_start, local_end = start - match.start(), end - match.start()
            changed = match[0][:local_start] + exact + match[0][local_end:]
            edits.append({'paragraph': position, 'original_expression': match[0],
                          'replacement': changed, 'reason': 'BOUND_EVENT_SCALE_ARITHMETIC',
                          'source_reference': row['source_reference'], 'reader_revision': row['reader_revision']})
            bad_scales.append((position, match['result'], match['unit'], row))
            return changed
        paragraphs[position] = _DIV.sub(repair, paragraph)
    for position, paragraph in enumerate(paragraphs):
        if not re.search(r'合同|中标|项目', paragraph) or not re.search(r'营收|营业收入|规模', paragraph):
            continue
        for expression_position, bad, unit, row in bad_scales:
            marker = re.search(r'(?<![\d.,])' + re.escape(bad) + r'\s*[*_]*\s*' + re.escape(unit), paragraph)
            if not marker or position == expression_position:
                continue
            preceding = paragraph[max(0, marker.start()-12):marker.start()]
            if re.search(r'并非|不是|不等于|不能|错误|不应', preceding):
                continue
            edits.append({'paragraph': position, 'original_paragraph': paragraph,
                          'reason': 'DEPENDENT_UNSUPPORTED_SCALE_INFERENCE',
                          'source_reference': row['source_reference'], 'reader_revision': row['reader_revision']})
            percent = format(Decimal(row['percent']).quantize(Decimal('0.01')), 'f')
            paragraphs[position] = ('经营规模仅作参照：项目总额约为' + row['annual_period'][:4]
                                    + '年度营收的' + percent + '%，不代表当年新增收入、利润或收入确认预测。')
            break
    return '\n\n'.join(paragraphs), edits


def repair_scale_units(reply,records):
    """Repair conflicting table units only when the inline amount is bound."""
    lines=reply.splitlines();unit_col=None;edits=[]
    for index,line in enumerate(lines):
        if not line.strip().startswith('|'):
            unit_col=None;continue
        if '\\|' in line:continue
        cells=line.strip().strip('|').split('|')
        plain=[re.sub(r'[*_`]','',cell).strip() for cell in cells]
        if '单位' in plain:
            unit_col=plain.index('单位');continue
        if unit_col is None or len(cells)<=unit_col or plain[unit_col] not in _FACTORS:continue
        for col,value in enumerate(plain):
            if col==unit_col:continue
            match=re.fullmatch(r'('+_NUM+r')\s*(亿元|万元|元)',value)
            if not match or match[2]==plain[unit_col]:continue
            candidates=[r for r in records if any(_source_matches(match[1],match[2],r[key])
                for key in ('amount_yuan','annual_revenue_yuan'))]
            if len({(r['amount_yuan'],r['annual_revenue_yuan'],r['annual_period']) for r in candidates})!=1:continue
            row=candidates[0];cells[col]=' '+match[1]+' ';cells[unit_col]=' '+match[2]+' '
            lines[index]='|'+'|'.join(cells)+'|'
            edits.append({'line':index,'original_line':line,'replacement':lines[index],
                'reason':'BOUND_TABLE_UNIT_CONFLICT','source_reference':row['source_reference'],
                'reader_revision':row['reader_revision']})
            break
    return '\n'.join(lines),edits
