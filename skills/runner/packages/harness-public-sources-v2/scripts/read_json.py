"""Read complete saved pages, returning narrow original field spans and a date index."""
import argparse
import json
from pathlib import Path
import re
import runpy


def field_spans(text, start):
    decoder=json.JSONDecoder()
    at=start+1
    while True:
        at=re.compile(r'\s*').match(text,at).end()
        if text[at]=='}':return
        key,end=decoder.raw_decode(text,at)
        value_start=re.compile(r'\s*:\s*').match(text,end).end()
        _,value_end=decoder.raw_decode(text,value_start)
        yield key,at,value_start,value_end
        at=re.compile(r'\s*').match(text,value_end).end()
        if text[at]=='}':return
        at+=1


def read_rows(path, where, fields, limit=2):
    legacy=runpy.run_path(str(Path(__file__).resolve().parents[2]/'harness-public-sources-v1/scripts/read_json.py'))
    original=legacy['read_rows'](path,where,fields,limit)
    document=json.loads(Path(path).read_text(encoding='utf-8'))
    pages={page['page']:page['text'] for page in document['pages']}
    excerpts,sources=[],[]
    available=set()
    for row,full in zip(original['rows'],original['source_excerpts']):
        text=pages[full['page']]
        row['raw_values']={}
        for key,start,value_start,end in field_spans(text,full['start']):
            available.add(key)
            if key not in set(where)|set(fields):continue
            quote=text[start:end]
            excerpts.append({**full,'start':start,'end':end,'quote':quote})
            sources.append({'id':full['source_id'],'locator':f"第{full['page']}页，行起点{full['start']}，字段{key}；"+json.dumps(where,ensure_ascii=False),'quote':quote})
            if key in fields:row['raw_values'][key]=text[value_start:end]
    dates=set()
    identity={k:v for k,v in where.items() if k!='REPORT_DATE'}
    decoder=json.JSONDecoder()
    for text in pages.values():
        for match in re.finditer(r'\{',text):
            try:value,_=decoder.raw_decode(text,match.start())
            except ValueError:continue
            if isinstance(value,dict) and isinstance(value.get('REPORT_DATE'),str) and all(value.get(k)==v for k,v in identity.items()):
                dates.add(value['REPORT_DATE'])
    return {'rows':original['rows'],'source_excerpts':excerpts,'sources':sources,
            'available_fields':sorted(available),'available_periods':sorted(dates,reverse=True),
            'reading':'values/raw_values是所选行；available_periods仅定位其他报告日期，不是已读报告。完整数据在pages[].text，不解析截断content。'}


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('path')
    parser.add_argument('--where',nargs='+',required=True)
    parser.add_argument('--fields',nargs='+',required=True)
    parser.add_argument('--limit',type=int,choices=range(1,7),default=2)
    args=parser.parse_args()
    print(json.dumps(read_rows(args.path,dict(item.split('=',1) for item in args.where),args.fields,args.limit),ensure_ascii=False))
