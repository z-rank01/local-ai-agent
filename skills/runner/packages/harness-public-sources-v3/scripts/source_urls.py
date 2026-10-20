"""Reuse existing public entries and add the raw Tencent quote entry."""
import argparse
import json
from pathlib import Path
import runpy

legacy=runpy.run_path(str(Path(__file__).resolve().parents[2]/'harness-public-sources-v1/scripts/source_urls.py'))


def source_url(kind, **args):
    if kind!='quote':
        return legacy['source_url'](kind,**args)
    symbol=args.get('symbol')
    legacy['source_url']('profile',symbol=symbol)  # Same explicit-symbol contract.
    return 'https://qt.gtimg.cn/q='+symbol.lower()


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('kind',choices=('quote','profile','finance','notices','notice'))
    for key in ('symbol','start','end','article'):p.add_argument('--'+key)
    p.add_argument('--page',type=int,default=1)
    print(json.dumps({'url':source_url(**vars(p.parse_args()))},ensure_ascii=False))
