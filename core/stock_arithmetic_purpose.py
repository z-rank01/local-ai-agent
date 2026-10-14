"""Present validated public calculations; never classify user intent.

Only an explicit model selection on a successful current report read can use
this path. Research analysis stays on the ordinary model path.
"""
import re
from decimal import Decimal,localcontext
from .stock_calculation_review import validate_calculations,validated_annual_source
from .stock_financial_receipts import currency_display
from .stock_calculation_contract import validated_service_mean

VERSION='program-arithmetic-reply-v1'


def request_error(params,question):
    focus=params.get('reply_focus','analysis')
    if focus not in ('analysis','calculation'):return '报告reply_focus只能是analysis或calculation。'
    if focus=='calculation':
        refs=set(re.findall(r'(?<![0-9a-f])[0-9a-f]{64}(?![0-9a-f])',question or ''))
        if refs and params.get('reference') not in refs:
            return '纯计算读取必须使用本轮指定任务引用；此步骤未执行，不自动替换引用。'
    return None



def calculation_reply(observation,params,question):
    if params.get('reply_focus')!='calculation' or request_error(params,question):return None
    try:
        if (not isinstance(observation,dict) or observation.get('kind')!='local_report_page'
                or observation.get('error') or observation.get('source_reference')!=params.get('reference')):return None
        pages=observation.get('answer_pages')
        if not isinstance(pages,list) or not pages:return None
        calcs=validate_calculations(observation.get('program_calculations',[]))
        if len(calcs)!=1:return None
        c=calcs[0]
        if not all(p.get('reader_revision')==c['reader_revision'] for p in pages):return None
        body=''.join(p['body'] for p in pages)
        if not all(c[k] in body for k in ('amount_yuan','annual_revenue_yuan','annual_period')):return None
        annual=validated_annual_source(c);mean=validated_service_mean(c,body)
        if annual is None or mean is None:return None
        total=currency_display(c['amount_yuan'])['万元'];revenue=currency_display(c['annual_revenue_yuan'])['万元']
        average=currency_display(mean['mean_amount_yuan'])['万元'];src=mean['duration_source']
        with localcontext() as ctx:
            ctx.prec=28
            percent=format(Decimal(c['percent']).quantize(Decimal('0.01')),'f')
            mean_percent=format(Decimal(mean['mean_percent']).quantize(Decimal('0.01')),'f')
        return (f"项目总金额为{total}万元，公告所示运营期限为{mean['service_years']}年；"
                f"{c['annual_period'][:4]}年度营业收入为{revenue}万元。总金额与该年度营收相比约{percent}%，这里只比较规模。\n\n"
                f"总金额÷运营年限，得到年均规模{average}万元，相当于上述年度营收约{mean_percent}%。"
                '这个算术平均不要求各年均匀分配；它不代表每年实际确认这些收入，也不能据此认定某年业绩影响有限或确定。'
                '要判断该项目实际收入，仍需核对签约、履约及确认安排；不能由这个除法补出这些进度。\n\n'
                f"期限原句：{src['quote']}（{src['published']}）。[公告来源](<{src['url']}>)；"
                f"[年度营收来源](<{annual['url']}>)。以上是已保存资料的计算参照，研究结论仍需结合其他证据。")
    except (ValueError,TypeError,KeyError,ArithmeticError):return None
