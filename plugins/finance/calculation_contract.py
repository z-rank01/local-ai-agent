"""Pure optional calculation validation, independent of presentation."""
import re
from decimal import Decimal, localcontext
from datetime import date
from urllib.parse import urlparse

def validated_service_mean(calc,body=None):
    mean=calc.get('service_mean')
    if mean is None:return None
    if not isinstance(mean,dict) or mean.get('version')!='event-service-mean-v1':raise ValueError('平均值版本无效')
    source=mean.get('duration_source')
    if (not isinstance(source,dict) or source.get('kind')!='ORIGINAL_TEXT' or source.get('symbol')!=calc['symbol']
            or not isinstance(source.get('quote'),str) or not source['quote']
            or not re.fullmatch('[0-9a-f]{16}',str(source.get('event_id','')))
            or not re.fullmatch(r'\d+:[0-9a-f]{16}',str(source.get('read_cursor','')))
            or not isinstance(source.get('location'),str) or not source['location']):raise ValueError('期限来源未绑定')
    url=urlparse(str(source.get('url','')))
    if url.scheme!='https' or url.hostname not in ('pdf.dfcfw.com','www.cninfo.com.cn') or url.username or url.password:
        raise ValueError('期限来源不是本次公告公开地址')
    date.fromisoformat(source['published'])
    match=re.fullmatch(r'运营期限\s*[：:]\s*(\d+(?:\.\d+)?)\s*年',source['quote'])
    if not match:raise ValueError('期限原句格式无效')
    with localcontext() as ctx:
        ctx.prec=28
        for key in ('service_years','mean_amount_yuan','mean_percent'):
            if not isinstance(mean.get(key),str) or not re.fullmatch(r'\d+(?:\.\d+)?',mean[key]):raise ValueError('平均值数值格式无效')
        years,n,percent=(Decimal(mean[k]) for k in ('service_years','mean_amount_yuan','mean_percent'))
        if years<=0 or years!=Decimal(match[1]) or n!=Decimal(calc['amount_yuan'])/years or percent!=n/Decimal(calc['annual_revenue_yuan'])*100:
            raise ValueError('平均值与已核验金额/期限/年度基数不一致')
        if body is not None and (source['quote'] not in body or mean['mean_amount_yuan'] not in body
                or format(percent.quantize(Decimal('0.01')),'f')+'%' not in body):raise ValueError('平均回执与同版正文不一致')
    return mean

