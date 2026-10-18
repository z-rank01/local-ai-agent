"""Generate offline developer documentation from checked-in tools and saved runs."""
import argparse
from datetime import datetime, timezone
from html import escape
import json
import sqlite3
from pathlib import Path
import sys

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from core.research_session import RESEARCH_TOOLS
PACKAGE = ROOT / 'skills/runner/packages/harness-stock-research-v2'
SCENARIOS = {
    '公司综合': ['company', 'finance', 'risk', 'delivery'],
    '公告影响': ['event', 'finance', 'delivery'],
    '同行比较': ['comparison', 'finance', 'risk', 'delivery'],
    '行业候选': ['industry', 'company', 'risk', 'delivery'],
}


def inventory(workspace, additional_workspaces=()):
    tools = []
    for path in sorted((ROOT/'config/tools').glob('*.yaml')):
        tool = yaml.safe_load(path.read_text(encoding='utf-8'))
        if tool['name'] in RESEARCH_TOOLS:
            tools.append({**tool, 'source_file': str(path.relative_to(ROOT))})
    methods = [{'name': p.stem, 'text': p.read_text(encoding='utf-8'),
                'source_file': str(p.relative_to(ROOT))} for p in sorted((PACKAGE/'references').glob('*.md'))]
    runs = []
    for root in dict.fromkeys((workspace, *additional_workspaces)):
        paths = [p for folder in ('research', 'chat-research')
                 for p in (root/'reports'/folder).glob('*/research.json')]
        for path in sorted(paths):
            run = json.loads(path.read_text(encoding='utf-8'))
            # Only explicitly selected workspaces; no scan of unrelated private roots.
            row = {k: run.get(k) for k in ('id', 'model', 'scope', 'started_at', 'phase', 'status',
                                          'directory', 'methods', 'trace', 'conversation_id')}
            row['workspace'] = 'default' if root == workspace else root.name
            report = path.parent/'report.html'
            row['report_url'] = report.resolve().as_uri() if report.is_file() else None
            runs.append(row)
    runs.sort(key=lambda row: row.get('started_at') or '', reverse=True)
    # Join only explicitly selected runs; never include model text or raw payloads.
    native = {row['conversation_id']: {} for row in runs}
    audit = ROOT/'data/logs/audit.jsonl'
    if audit.exists():
        for line in audit.open(encoding='utf-8'):
            if not line.endswith('\n'):  # A writer may still be appending the last record.
                continue
            event = json.loads(line)
            session = event.get('session_id')
            if session not in native or event.get('event') not in ('native_model_request', 'native_model_result'):
                continue
            attempt = native[session].setdefault(event['local_attempt_id'], {})
            if event['event'] == 'native_model_request':
                attempt.update(at=event.get('ts'), model=event.get('model'), input_chars=event.get('input_chars'))
            else:
                attempt.update({key: event.get(key) for key in
                    ('result_status', 'delivery_status', 'finish_reason', 'failure_category', 'elapsed_seconds', 'usage')})
    for row in runs:
        row['native_calls'] = list(native[row['conversation_id']].values())
    # A conversation can contain several research versions. Match actual
    # server-owned executions, rather than showing the whole conversation's
    # calls as if each version made them all.
    database = ROOT/'data/conversations.db'
    managed = {}
    if database.is_file():
        from contextlib import closing
        with closing(sqlite3.connect(database.resolve().as_uri()+'?mode=ro', uri=True)) as db:
            if db.execute("SELECT 1 FROM sqlite_master WHERE name='chat_runs'").fetchone():
                for identifier, status, start, end, checkpoint in db.execute(
                        'SELECT id,status,created_at,updated_at,checkpoint FROM chat_runs'):
                    research = (json.loads(checkpoint).get('research') or {}).get('record') or {}
                    if research.get('id'):
                        managed.setdefault(research['id'], []).append({'id':identifier, 'status':status,
                            'start':start, 'end':end})
    for row in runs:
        row['executions'] = managed.get(row['id'], [])
        row['native_calls'], row['native_scope'] = scoped_calls(row['native_calls'], row['executions'])
    return {'tools': tools, 'methods': methods, 'runs': runs, 'scenarios': SCENARIOS,
            'generated_at': datetime.now(timezone.utc).isoformat()}


def scoped_calls(calls, executions):
    if not executions:
        return calls, '会话级历史记录；没有运行关联，不作为单份研究的调用总数'
    return [call for call in calls if any(run['start'] <= (call.get('at') or '') <= run['end']
            for run in executions)], '本研究关联运行（含人工续行），按实际执行时间范围归属'


def render(data):
    payload = json.dumps(data, ensure_ascii=False).replace('<', '\\u003c')
    return r'''<!doctype html><html lang="zh-CN"><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; base-uri 'none'">
<title>聊天投研工具包 · 开发者导览</title><style>
body{overflow-wrap:anywhere;margin:0;background:#f3f5f1;color:#21332c;font:16px/1.7 system-ui,sans-serif}main{max-width:1120px;margin:auto;padding:32px 22px}h1{font-size:32px}h2{margin-top:36px}nav{display:flex;gap:12px;flex-wrap:wrap}a{color:#285e4b}section,details{background:white;border:1px solid #d3dcd5;border-radius:12px;padding:18px;margin:12px 0}summary{cursor:pointer;font-weight:600}.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(260px,1fr));gap:12px}pre{white-space:pre-wrap;overflow-wrap:anywhere;font:13px/1.6 monospace}.badge{display:inline-block;background:#e4eee8;border-radius:20px;padding:2px 10px}.warn{background:#f7e8cf}svg{width:100%;height:auto}select{font:inherit;max-width:100%;padding:8px}table{width:100%;border-collapse:collapse}td,th{border-bottom:1px solid #ddd;text-align:left;padding:8px;overflow-wrap:anywhere}.scroll{overflow:auto}button{font:inherit}small{color:#53645b}@media print{nav,select{display:none}section,details{break-inside:avoid}body{background:white}}</style>
<main><header><span class="badge">开发文档 · 非生产依赖</span><h1>聊天 Agent 如何完成投研</h1><p>模型选择方法、来源和判断。程序负责工作区权限、计算核对、保存与排版。图示路径不代表每次都会执行。</p><small id="stamp"></small></header>
<nav><a href="#map">能力地图</a><a href="#flow">场景流程</a><a href="#replay">真实回放</a><a href="#gaps">能力与缺口</a></nav>
<section id="map"><h2>能力地图</h2><span class="badge">结构示意</span>
<svg viewBox="0 0 1000 210" role="img" aria-label="模型按需读方法和取证，程序保存证据并发布报告">
<g fill="#e4eee8" stroke="#7b9788"><rect x="10" y="30" width="220" height="70" rx="10"/><rect x="270" y="30" width="220" height="70" rx="10"/><rect x="530" y="30" width="220" height="70" rx="10"/><rect x="790" y="30" width="200" height="70" rx="10"/></g>
<g font-size="19" text-anchor="middle" fill="#21332c"><text x="120" y="60">问题 → 选择方法</text><text x="120" y="86">模型决策</text><text x="380" y="60">取证 → 计算 → 反证</text><text x="380" y="86">模型调用现有工具</text><text x="640" y="60">核对 → 发布</text><text x="640" y="86">程序检查与排版</text><text x="890" y="60">HTML → 报告卡</text><text x="890" y="86">原工作区 / 阅读器</text></g>
<g stroke="#53645b" stroke-width="3"><path d="M230 65H270M490 65H530M750 65H790"/></g><text x="10" y="150" font-size="18">来源、字段、计算与模型判断分别保存；UNKNOWN 不自动重发。</text><text x="10" y="182" font-size="18">方法是笔记，不是事实。结构检查不是语义认证。</text></svg>
<h3>实际方法文件</h3><div id="methods" class="grid"></div><h3>研究工具静态定义</h3><p>清单取自当前研究允许集合和对应YAML；开关、授权仍决定每轮实际可用工具。context_evidence_read由Agent按已登记证据动态提供，定义见core/agent.py。</p><div id="tools" class="grid"></div>
<details><summary>业务模块与产物</summary><pre>core/research_session.py：会话研究记录、容器文件保存、发布
core/research_visuals.py：发布输入核对、SVG
core/research_material_view.py：旧大工具回执和参数转为可完整回读的证据指针
core/brief_document.py：既有阅读模板
skills/websearch/source_document.py：PDF页码、快照与分页
skills/runner/packages/harness-stock-research-v2/scripts/calculate.py：容器算术
skills/runner/packages/harness-stock-research-v2/scripts/extract.py：批量原文摘录，精确页码与位置；收到并核对的原句才登记为已读
股票侧：既有公开字段与金融口径，旧专业任务不由本流程恢复
产物：research.json、sources/、model-original.md、report.md、evidence.json、report.html
publication-attempts/N.json：每次实际发布输入；失败后复制工作稿定向修订，再传draft_path</pre></details></section>
<section id="flow"><h2>场景流程</h2><span class="badge warn">可能路径 · 非执行证据</span><p><select id="scenario"></select></p><div id="scenario-body"></div></section>
<section id="replay"><h2>真实运行回放</h2><p>读取所选工作区实际research.json，并关联现有日志中的模型诊断字段。保存状态不是实时运行状态；终态未记录不能当成功或失败。只统计已记录调用，不补猜旧调用或未知用量。隐藏思维链、正文、密钥和完整请求均不展示。</p><select id="run"></select><div id="run-body"></div></section>
<section id="gaps"><h2>能力与缺口</h2><ul><li>已实现：方法安装、来源分页、容器算术、研究记录和HTML发布。</li><li>实测范围：以本页真实回放和验证记录为准，不能以工具存在推定成功。</li><li>扫描PDF需OCR；当前明确报错，不认证空文本。</li><li>来源冲突和业务因果由模型核查，程序不认证语义；权限、停止及错误需结合验收记录。</li><li>四场景、命名工作区、停止和中断尚未全部完成验收。</li></ul><p>验证记录：docs/记录/2026-10-18-聊天自主投研闭环.md；当前进度：股票 docs/计划.md。</p></section></main>
<script>const data=PAYLOAD;
const el=id=>document.getElementById(id);const text=(tag,s)=>{const n=document.createElement(tag);n.textContent=s;return n};
el('stamp').textContent='生成于 '+data.generated_at+'；重新生成可刷新实际定义和回放。';
for(const m of data.methods){const d=document.createElement('details');d.append(text('summary',m.name),text('small',m.source_file),text('pre',m.text));el('methods').append(d)}
for(const t of data.tools){const d=document.createElement('details');d.append(text('summary',t.name+' · '+t.backend),text('p',t.description),text('small',t.source_file),text('pre',JSON.stringify(t.parameters,null,2)));el('tools').append(d)}
for(const k of Object.keys(data.scenarios)){const o=text('option',k);o.value=k;el('scenario').append(o)}
function scenario(){const box=el('scenario-body');box.replaceChildren(text('p','准备：research_begin → 读取 '+data.scenarios[el('scenario').value].join('、')),text('p','获取：web_search 发现线索 → web_fetch 读原文；必要时读股票已保存来源。'),text('p','分析：容器计算、比较与反证；出现冲突则补查原文，资料不足则收窄结论。'),text('p','核对与交付：检查引用及数字 → brief_publish → 当前对话报告卡。失败保留记录，不伪造交付。'))}el('scenario').onchange=scenario;scenario();
for(const [i,r] of data.runs.entries()){const o=text('option',r.scope+' · '+r.id.slice(0,8));o.value=i;el('run').append(o)}
function table(box,head,rows){const t=document.createElement('table');const h=document.createElement('tr');for(const v of head)h.append(text('th',v));t.append(h);for(const row of rows){const tr=document.createElement('tr');for(const v of row)tr.append(text('td',v));t.append(tr)}const wrap=document.createElement('div');wrap.className='scroll';wrap.append(t);box.append(wrap)}
function replay(){const box=el('run-body');box.replaceChildren();const r=data.runs[el('run').value];if(!r){box.append(text('p','当前没有实际运行记录。'));return}box.append(text('p','模型：'+r.model+'；保存阶段：'+r.phase+'；保存状态：'+r.status),text('p','工作区：'+r.workspace+'；产物：'+r.directory),text('pre','实际读取方法：\n'+(r.methods||[]).join('\n')));if(r.report_url){const a=text('a','打开该版本HTML报告');a.href=r.report_url;a.target='_blank';a.rel='noopener';box.append(a)}box.append(text('p',r.native_scope),text('p','关联运行：'+(r.executions||[]).map(x=>x.id+' / '+x.status).join('；')));box.append(text('h3','工具步骤'));table(box,['阶段','工具','状态','耗时','位置'],(r.trace||[]).map(row=>[row.phase,row.tool,row.status,row.elapsed+'秒',row.path||row.source_id||'']));box.append(text('h3','已记录模型调用（'+r.native_calls.length+'）'));table(box,['请求时间','输入字符','供应商 / 本地交付','耗时','实际用量'],r.native_calls.map(n=>[n.at||'未记录',n.input_chars??'未知',(n.result_status||'终态未记录')+' / '+(n.delivery_status||'未知')+(n.failure_category?' · '+n.failure_category:''),n.elapsed_seconds==null?'未知':n.elapsed_seconds+'秒',n.usage==null?'未知':JSON.stringify(n.usage)]))}el('run').onchange=replay;replay();</script></html>'''.replace('PAYLOAD', payload)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--workspace', type=Path, default=ROOT/'data/workspace')
    parser.add_argument('--include-workspace', type=Path, action='append', default=[])
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    output = args.output or args.workspace/'developer/research-toolkit/index.html'
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(render(inventory(args.workspace, args.include_workspace)), encoding='utf-8')
    print(output)
