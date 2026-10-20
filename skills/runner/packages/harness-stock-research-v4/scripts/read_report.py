"""Read exact windows and expose matching pages from a saved source, offline."""
import argparse
import json
from pathlib import Path


def read_report(path, terms, pages=None, limit=12, context=400):
    if not terms or any(not isinstance(t,str) or not t.strip() for t in terms):
        raise ValueError('terms must contain actual nonempty search text')
    if limit < 1 or context < 0:
        raise ValueError('limit/context out of range')
    saved=json.loads(Path(path).read_text(encoding='utf-8'))
    selected=set(pages) if pages is not None else None
    groups={term:[] for term in dict.fromkeys(terms)}
    matches={term:[] for term in groups}
    for page in saved['pages']:
        text=page['text']
        positions=[i for i,c in enumerate(text) if not c.isspace()]
        compact=''.join(text[i] for i in positions)
        for term in groups:
            at=compact.find(''.join(term.split()))
            if at < 0:
                continue
            matches[term].append(page['page'])
            if selected is not None and page['page'] not in selected:
                continue
            start=max(0,positions[at]-context)
            end=min(len(text),positions[at+len(''.join(term.split()))-1]+1+context)
            groups[term].append({'source_id':Path(path).stem,'sha256':saved['sha256'],
                'page':page['page'],'start':start,'end':end,'quote':text[start:end]})
    # Round-robin across terms: a common first term must not consume every slot.
    found=[]
    while any(groups.values()) and len(found)<limit:
        for rows in groups.values():
            if not rows:
                continue
            row=rows.pop(0)
            if not any(r['page']==row['page'] and r['start']<=row['start'] and r['end']>=row['end'] for r in found):
                found.append(row)
            if len(found)==limit:
                break
    return {'source_excerpts':found,'match_pages':matches,
        'reading':'match_pages是程序检索目录；只有source_excerpts为本次实际返回原文，不代表全文阅读或财务含义认证'}


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('path')
    p.add_argument('--terms',nargs='+',required=True)
    p.add_argument('--pages',nargs='+',type=int)
    p.add_argument('--limit',type=int,default=12)
    p.add_argument('--context',type=int,default=400)
    args=vars(p.parse_args())
    print(json.dumps(read_report(**args),ensure_ascii=False))
