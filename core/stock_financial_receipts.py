"""Validate optional financial metadata against this actual source-read page.

This checks source pairing and exact currency conversion, never business
comparability, market confirmation or investment quality. Old source text and
read cursors remain unchanged if optional metadata is missing or invalid.
"""
from collections import Counter
from datetime import date
from decimal import Decimal, InvalidOperation, localcontext
import re
from urllib.parse import urlparse
from .stock_company_context import FINANCIAL_METRICS

HOSTS={'www.csrc.gov.cn','www.ndrc.gov.cn','pdf.dfcfw.com','data.eastmoney.com','vip.stock.finance.sina.com.cn'}

def _number(value):
    if not isinstance(value,str) or not re.fullmatch(r'-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?',value):
        raise ValueError('非来源数值')
    number=Decimal(value)
    if not number.is_finite():raise ValueError('非有限值')
    return number

def currency_display(value):
    """Format one already validated CNY scalar, without choosing its source.

    Source, subject, period and business calculation remain the caller's
    responsibility. This does not parse model prose or choose a denominator.
    """
    number = _number(value)
    with localcontext() as ctx:
        ctx.prec = max(28, len(number.as_tuple().digits) + 10)
        def fixed(divisor):
            text = format(number / Decimal(divisor), 'f')
            return text.rstrip('0').rstrip('.') if '.' in text else text
        return {'元': fixed(1), '万元': fixed(10000), '亿元': fixed(100000000)}


def financial_fields_from_read(observation):
    fields=observation.get('financial_fields')
    if not isinstance(fields,list) or observation.get('kind')!='public_evidence_read':return []
    spans=observation.get('excerpts')
    if not isinstance(spans,list):return []
    counts=Counter((f.get('symbol'),f.get('financial_metric'),f.get('report_period'))
        for f in fields if isinstance(f,dict) and all(isinstance(f.get(k),str) for k in ('symbol','financial_metric','report_period')))
    result=[]
    for row in fields:
        try:
            if not isinstance(row,dict):continue
            symbol,day,metric=row['symbol'],row['report_period'],row['financial_metric']
            if (row.get('version')!='financial-read-field-v1' or row.get('unit')!='CNY'
                    or not re.fullmatch(r'\d{6}',str(symbol)) or not isinstance(row.get('name'),str) or not row['name']
                    or metric not in FINANCIAL_METRICS or not re.fullmatch(r'[12]\d{7}',str(day))
                    or day[4:] not in ('0331','0630','0930','1231') or counts[(symbol,metric,day)]!=1
                    or not isinstance(row.get('field'),str) or not row['field']):continue
            date.fromisoformat(day[:4]+'-'+day[4:6]+'-'+day[6:])
            raw=_number(row['raw_value'])
            with localcontext() as ctx:
                ctx.prec=max(28,len(raw.as_tuple().digits)+10)
                if (raw!=_number(row['value_yuan']) or raw/Decimal(10000)!=_number(row['value_wan_yuan'])
                        or raw/Decimal(100000000)!=_number(row['value_yi_yuan'])):continue
            url=urlparse(str(row.get('url','')))
            if url.scheme!='https' or url.hostname not in HOSTS or url.username or url.password:continue
            matches=[s for s in spans if isinstance(s,dict) and s.get('excerpt_id')==row.get('excerpt_id')]
            if len(matches)!=1:continue
            span=matches[0]
            primary=symbol==observation.get('symbol')
            expected_kind='SOURCE_FIELD' if primary else 'PEER_SOURCE_FIELD'
            if (row.get('kind')!=expected_kind or span.get('kind')!=expected_kind
                    or (span.get('symbol') or observation.get('symbol'))!=symbol
                    or not isinstance(row.get('location'),str) or not row['location']
                    or span.get('location')!=row['location'] or span.get('url')!=row['url']
                    or row.get('published')!=span.get('published')):continue
            expected=(f"{row['field']}：{row['raw_value']}；期间 {day}；币种/单位 CNY；披露 {row.get('published') or '未确认'}" if primary
                else f"同行{symbol} {row['field']}：{row['raw_value']}；期间{day}；单位CNY")
            if span.get('text')!=expected:continue
            result.append({k:row[k] for k in ('version','symbol','name','financial_metric','field','report_period','unit',
                'raw_value','value_yuan','value_wan_yuan','value_yi_yuan','excerpt_id','kind','url','location')})
            if row.get('published') is not None:result[-1]['published']=row['published']
        except (InvalidOperation,KeyError,TypeError,ValueError):continue
    return result
