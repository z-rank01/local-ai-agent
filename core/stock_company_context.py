"""Validate a paired, public comparison basis without certifying model reasoning."""
import json
import re
from decimal import Decimal, InvalidOperation
from urllib.parse import urlparse


def validate_context(context, pages):
    if (not isinstance(context, dict) or context.get('version') != 'comparison-source-context-v1'
            or context.get('comparable_period') not in ('YES', 'NO')
            or not re.fullmatch('[0-9a-f]{64}', str(context.get('reader_revision', '')))
            or not pages or any(p.get('reader_revision') != context['reader_revision'] for p in pages)):
        raise ValueError('比较依据或展示版本无效')
    text=''.join(p['body'] for p in pages)
    subjects=context.get('subjects')
    if not isinstance(subjects,list) or len(subjects)<2:
        raise ValueError('比较对象未完整交付')
    codes=set()
    for i,subject in enumerate(subjects):
        code=subject.get('symbol');name=subject.get('name')
        if (not re.fullmatch(r'\d{6}',str(code)) or code in codes
                or not isinstance(name,str) or not name or code not in text or name not in text
                or subject.get('role')!=('primary' if i==0 else 'comparison')):
            raise ValueError('比较身份与正文不一致')
        codes.add(code)
        metrics=subject.get('metrics')
        if not isinstance(metrics,list) or {m.get('metric') for m in metrics} != {'revenue','parent_profit','ocf'} or len(metrics)!=3:
            raise ValueError('比较口径无效')
        for row in metrics:
            if row.get('status')=='MISSING':continue
            if (row.get('status')!='SOURCE_BOUND' or row.get('unit')!='CNY'
                    or row.get('kind')!=('SOURCE_FIELD' if i==0 else 'PEER_SOURCE_FIELD')
                    or not re.fullmatch(r'[12]\d{7}',str(row.get('report_period')))
                    or row['report_period']!=subject.get('report_period')
                    or row['report_period'] not in text or row.get('value_yuan','!') not in text
                    or not row.get('location') or not row.get('field')
                    or not re.fullmatch('[0-9a-f]{16}',str(row.get('event_id','')))
                    or not re.fullmatch(r'\d+:[0-9a-f]{16}',str(row.get('read_cursor','')))):
                raise ValueError('比较字段定位无效')
            url=urlparse(str(row.get('url','')))
            if url.scheme!='https' or url.hostname!='vip.stock.finance.sina.com.cn' or url.username or url.password:
                raise ValueError('比较财务来源无效')
            try:
                number=Decimal(row['value_yuan'])
                if not number.is_finite() or number!=Decimal(row['raw_value']):raise ValueError('比较字段数值不一致')
            except (InvalidOperation,KeyError,TypeError):raise ValueError('比较字段数值无效') from None
        if context['comparable_period']=='YES' and subject.get('report_period')!=context.get('common_period'):
            raise ValueError('共同期间不一致')
    return context


def context_index(messages):
    calls,latest={},{}
    for message in messages:
        for call in message.get('tool_calls') or []:
            fn=call.get('function') or {}
            if fn.get('name')=='stock_report_read':
                try:
                    args=fn.get('arguments',{})
                    args=json.loads(args) if isinstance(args,str) else args
                    calls[call['id']]=args['reference']
                except (ValueError,KeyError,TypeError):pass
        if message.get('role')!='tool' or message.get('tool_name')!='stock_report_read':continue
        ref=calls.get(message.get('tool_call_id'))
        if not ref:continue
        try:
            envelope=json.loads(message.get('content') or '{}')
            if envelope.get('error') or envelope.get('status') in ('FAILED','ERROR','RULE_BLOCKED'):continue
            obs=envelope.get('model_observation',envelope)
            if obs.get('kind')!='local_report_page' or obs.get('source_reference')!=ref:continue
            latest[ref]=None
            if obs.get('comparison_context'):
                latest[ref]=validate_context(obs['comparison_context'],obs.get('answer_pages'))
        except (ValueError,TypeError,AttributeError,KeyError):continue
    return latest


def current_guidance(index,question,source_index):
    refs=set(re.findall(r'(?<![0-9a-f])[0-9a-f]{64}(?![0-9a-f])',question))
    if not refs and source_index:refs={source_index[0]['source_reference']}
    rows=[{'source_reference':ref,**value} for ref,value in index.items() if ref in refs and value]
    if not rows:return None
    return ('当前公司比较的已授权身份和指标来源索引：\n'+json.dumps(rows,ensure_ascii=False)+
            '\n证券名称和代码按冻结对象逐一对应；SOURCE_BOUND字段已经保存在报告中，不能因名称检索未命中就说没有同行财务。'
            '必要核对时用同一source_reference、event_id和read_cursor回读，归母利润与合并净利润分开。'
            'MISSING仅表示该字段未通过绑定；共同期为空时分列，不能直接排名。'
            '这是结构化来源字段，不是年报逐字原文，不认证业务可比性或投资结论；报告仍是部分成果。'
            '额外资产、负债、单季或TTM陈述按对应证券、指标和期间回读来源；人民币元换算万元除10000、亿元除100000000，比较前核对双方单位。'
            '不能只凭经营现金流净额较高认定回款质量更好；还须核对营运资金、支付和收款构成。'
            '业务交集和客户关系需要业务原文；财务字段的绑定状态不能作为业务已证实的来源等级。'
            '回答使用公司名称、易懂指标与真实链接，不展示内部枚举、引用哈希或游标。')
