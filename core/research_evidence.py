"""Prepare evidence tools and model views; no financial interpretation."""
from copy import deepcopy
from datetime import date
from importlib import import_module
import json


def model_evidence(index):
    """Keep each source's full information without contained duplicate text."""
    index=deepcopy(index)
    for source in index.get('sources',{}).values():
        texts=list(dict.fromkeys(source.get('received',[])))
        source['received']=[text for text in texts if not any(text!=other and text in other for other in texts)]
    return index


def evidence_steps(request):
    source_url = import_module('skills.runner.packages.harness-public-sources-v3.scripts.source_urls').source_url
    kind = request['kind']
    args = {key: request[key] for key in ('symbol', 'start', 'end', 'article', 'page') if key in request}
    steps = []
    if kind == 'finance':
        today = date.today()
        steps.append({'name':'file_read','params':{'path':'/workspace/skills/harness-stock-research-v4/references/finance.md'},
                      'purpose':'读取财务期间、口径和推导方法'})
        # A directory is a version-discovery aid, not a chosen financial report.
        steps.append({'name': 'web_fetch', 'params': {'url': source_url('notices',
            symbol=request['symbol'], start=request.get('start', f'{today.year-1}-01-01'),
            end=request.get('end', today.isoformat()))}, 'purpose': '定位适用定期报告、快报及更正；目录不等于已读报告'})
        args = {'symbol': request['symbol']}
    steps.append({'name': 'web_fetch', 'params': {'url': source_url(kind, **args)},
                  'purpose': request.get('purpose', kind)})
    if kind == 'finance':
        for article in dict.fromkeys(request.get('report_articles', [])):
            steps.append({'name':'web_fetch', 'params':{'url':source_url('notice',article=article)},
                          'purpose':'读取模型从目录选定的定期报告原件；不认证所选版本适用性'})
    return steps


def report_read_step(path, terms, pages=None):
    """Read model-selected terms from saved pages through the existing executor."""
    args = json.dumps(json.dumps([path, terms, pages], ensure_ascii=False))
    code = ('import json, runpy\n'
            'reader=runpy.run_path("/workspace/skills/harness-stock-research-v4/scripts/read_report.py")\n'
            f'args=json.loads({args})\n'
            'print(json.dumps(reader["read_report"](*args),ensure_ascii=False))')
    return {'name':'code_exec','params':{'code':code},'purpose':'实际读取所选报告章节并返回原页位置；匹配目录不代表读完报告'}


def quote_read_step(path, symbol):
    args=json.dumps(json.dumps([path,symbol]))
    code=('import json, runpy\n'
          'reader=runpy.run_path("/workspace/skills/harness-public-sources-v3/scripts/read_quote.py")\n'
          f'args=json.loads({args})\n'
          'print(json.dumps(reader["read_quote"](*args),ensure_ascii=False))')
    return {'name':'code_exec','params':{'code':code},'purpose':'读取实际报价字段及原始行情时间，不把抓取时间当行情时间'}


def financial_read_step(request, source):
    """Use the installed reader with model-selected exact provider keys."""
    code = ('import json, runpy\n'
            'reader=runpy.run_path("/workspace/skills/harness-public-sources-v2/scripts/read_json.py")\n'
            f'args=json.loads({json.dumps(json.dumps([source, request["where"], request["fields"], 2]), ensure_ascii=False)})\n'
            'print(json.dumps(reader["read_rows"](*args),ensure_ascii=False))')
    return {'name': 'code_exec', 'params': {'code': code}, 'purpose': '按实际字段和期间读取已登记财务原值'}


def citation_read_step(needs):
    """Read the model-selected citations through the executor, never backfill coverage."""
    code = '''import json
from pathlib import Path
needs=json.loads(PAYLOAD)
excerpts=[]
missing=[]
for item in needs:
    saved=json.loads(Path(item['path']).read_text(encoding='utf-8'))
    found=False
    for page in saved['pages']:
        text=page['text']
        positions=[i for i,c in enumerate(text) if not c.isspace()]
        compact=''.join(text[i] for i in positions)
        needle=''.join(item['quote'].split())
        at=compact.find(needle)
        if at < 0 or not needle: continue
        start,end=positions[at],positions[at+len(needle)-1]+1
        excerpts.append({'source_id':item['source_id'],'sha256':saved['sha256'],
            'page':page['page'],'start':start,'end':end,'quote':text[start:end]})
        found=True
        break
    if not found:missing.append({'source_id':item['source_id'],'requested_quote':item['quote']})
print(json.dumps({'source_excerpts':excerpts,'unmatched':missing},ensure_ascii=False))
'''.replace('PAYLOAD',json.dumps(json.dumps(needs,ensure_ascii=False)))
    return {'name':'code_exec','params':{'code':code},'purpose':'实际回读当前稿已选择的原文引句；不修改原文或正文'}


def catalogue(pages):
    """Compact the actual provider directory; retain dates and identity verbatim."""
    from skills.websearch.source_document import json_body
    for page in pages:
        value = json_body(page['text'])
        if not isinstance(value, dict) or not isinstance(value.get('data'), dict):
            continue
        data = value['data']
        if isinstance(data.get('list'), list):
            return {'entries': [{k: row[k] for k in ('art_code', 'title', 'notice_date', 'codes', 'columns') if k in row}
                                for row in data['list']], 'page_index': data.get('page_index'),
                    'total_hits': data.get('total_hits'), 'reading': '目录定位，不是公告全文；版本类别按实际标题核对'}
    return None
