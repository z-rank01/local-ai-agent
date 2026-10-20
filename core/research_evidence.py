"""Expand explicit evidence needs into existing tools; no financial interpretation."""
from datetime import date
from importlib import import_module
import json


def evidence_steps(request):
    source_url = import_module('skills.runner.packages.harness-public-sources-v1.scripts.source_urls').source_url
    kind = request['kind']
    args = {key: request[key] for key in ('symbol', 'start', 'end', 'article', 'page') if key in request}
    steps = []
    if kind == 'finance':
        today = date.today()
        # A directory is a version-discovery aid, not a chosen financial report.
        steps.append({'name': 'web_fetch', 'params': {'url': source_url('notices',
            symbol=request['symbol'], start=request.get('start', f'{today.year-1}-01-01'),
            end=request.get('end', today.isoformat()))}, 'purpose': '定位适用定期报告、快报及更正；目录不等于已读报告'})
        args = {'symbol': request['symbol']}
    steps.append({'name': 'web_fetch', 'params': {'url': source_url(kind, **args)},
                  'purpose': request.get('purpose', kind)})
    return steps


def financial_read_step(request, source):
    """Use the installed reader with model-selected exact provider keys."""
    code = ('import json, runpy\n'
            'reader=runpy.run_path("/workspace/skills/harness-public-sources-v1/scripts/read_json.py")\n'
            f'args=json.loads({json.dumps(json.dumps([source, request["where"], request["fields"], 2]), ensure_ascii=False)})\n'
            'print(json.dumps(reader["read_rows"](*args),ensure_ascii=False))')
    return {'name': 'code_exec', 'params': {'code': code}, 'purpose': '按实际字段和期间读取已登记财务原值'}


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
