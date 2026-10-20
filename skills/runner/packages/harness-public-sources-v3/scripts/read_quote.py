"""Extract declared vendor fields and exact spans; never evaluate JavaScript."""
import argparse
import json
from pathlib import Path
import re


def read_quote(path, symbol):
    if not re.fullmatch(r'(SH|SZ|BJ)\d{6}',symbol):
        raise ValueError('Use the verified exchange-qualified symbol')
    saved=json.loads(Path(path).read_text(encoding='utf-8'))
    for page in saved['pages']:
        text=page['text']
        match=re.search(r'v_([a-z]{2}\d{6})="([^"\r\n]*)"',text)
        if match is None:continue
        fields=match.group(2).split('~')
        if match.group(1)!=symbol.lower() or len(fields)<=30 or fields[2]!=symbol[2:]:
            raise ValueError('Returned security does not match the selected symbol')
        if not re.fullmatch(r'\d+(?:\.\d+)?',fields[3]) or not re.fullmatch(r'\d{14}',fields[30]):
            raise ValueError('No valid raw price or quote timestamp')
        excerpts=[]
        for start,end in ((match.start(),match.start(2)+sum(len(v)+1 for v in fields[:4])-1),
                          (match.start(2)+sum(len(v)+1 for v in fields[:30]),
                           match.start(2)+sum(len(v)+1 for v in fields[:30])+len(fields[30]))):
            excerpts.append({'source_id':Path(path).stem,'sha256':saved['sha256'],'page':page['page'],
                             'start':start,'end':end,'quote':text[start:end]})
        return {'raw_values':{'name':fields[1],'code':fields[2],'price':fields[3],'quote_time':fields[30]},
                'source_excerpts':excerpts,'reading':'接口原字段，不认证报价实时性、币种、收盘属性或估值。'}
    raise ValueError('No recognized quote row; cannot invent a price')


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('path');p.add_argument('symbol')
    print(json.dumps(read_quote(**vars(p.parse_args())),ensure_ascii=False))
