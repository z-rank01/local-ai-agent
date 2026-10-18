"""Generate offline developer documentation from checked-in tools and saved runs."""
import argparse
from datetime import datetime, timezone
from html import escape
import json
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / 'skills/runner/packages/harness-stock-research-v1'
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
        if tool['name'] in ('research_begin', 'brief_publish', 'web_search', 'web_fetch',
                            'file_read', 'file_write', 'code_exec', 'shell_exec',
                            'skill_list', 'skill_info', 'context_evidence_read') or tool['name'].startswith('stock_public_evidence'):
            tools.append({**tool, 'source_file': str(path.relative_to(ROOT))})
    methods = [{'name': p.stem, 'text': p.read_text(encoding='utf-8'),
                'source_file': str(p.relative_to(ROOT))} for p in sorted((PACKAGE/'references').glob('*.md'))]
    runs = []
    for root in dict.fromkeys((workspace, *additional_workspaces)):
        for path in sorted((root/'reports/research').glob('*/research.json')):
            run = json.loads(path.read_text(encoding='utf-8'))
            # Only explicitly selected workspaces; no scan of unrelated private roots.
            row = {k: run.get(k) for k in ('id', 'model', 'scope', 'started_at', 'phase', 'status',
                                          'directory', 'methods', 'trace')}
            row['workspace'] = 'default' if root == workspace else root.name
            runs.append(row)
    return {'tools': tools, 'methods': methods, 'runs': runs, 'scenarios': SCENARIOS,
            'generated_at': datetime.now(timezone.utc).isoformat()}


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
<h3>实际方法文件</h3><div id="methods" class="grid"></div><h3>实际工具定义</h3><div id="tools" class="grid"></div>
<details><summary>业务模块与产物</summary><pre>core/research_session.py：会话研究记录、容器文件保存、发布
core/research_visuals.py：发布输入核对、SVG
core/brief_document.py：既有阅读模板
skills/websearch/source_document.py：PDF页码、快照与分页
skills/runner/packages/harness-stock-research-v1/scripts/calculate.py：容器算术
股票侧：既有公开字段与金融口径，旧专业任务不由本流程恢复
产物：research.json、sources/、model-original.md、report.md、evidence.json、report.html</pre></details></section>
<section id="flow"><h2>场景流程</h2><span class="badge warn">可能路径 · 非执行证据</span><p><select id="scenario"></select></p><div id="scenario-body"></div></section>
<section id="replay"><h2>真实运行回放</h2><p>只读取工作区实际 research.json。未记录交付不表示仍在运行，也不表示供应商失败。隐藏思维链、密钥和完整请求均不展示。</p><select id="run"></select><div id="run-body"></div></section>
<section id="gaps"><h2>能力与缺口</h2><ul><li>已实现：方法安装、来源分页、容器算术、研究记录和HTML发布。</li><li>实测范围：以本页真实回放和验证记录为准，不能以工具存在推定成功。</li><li>扫描PDF需OCR；当前明确报错，不认证空文本。</li><li>来源冲突和业务因果由模型核查，程序不认证语义；权限、停止及错误需结合验收记录。</li><li>四场景、命名工作区、停止和中断尚未全部完成验收。</li></ul><p>验证记录：docs/记录/2026-10-18-聊天自主投研闭环.md；当前进度：股票 docs/计划.md。</p></section></main>
<script>const data=PAYLOAD;
const el=id=>document.getElementById(id);const text=(tag,s)=>{const n=document.createElement(tag);n.textContent=s;return n};
el('stamp').textContent='生成于 '+data.generated_at+'；重新生成可刷新实际定义和回放。';
for(const m of data.methods){const d=document.createElement('details');d.append(text('summary',m.name),text('small',m.source_file),text('pre',m.text));el('methods').append(d)}
for(const t of data.tools){const d=document.createElement('details');d.append(text('summary',t.name+' · '+t.backend),text('p',t.description),text('small',t.source_file),text('pre',JSON.stringify(t.parameters,null,2)));el('tools').append(d)}
for(const k of Object.keys(data.scenarios)){const o=text('option',k);o.value=k;el('scenario').append(o)}
function scenario(){const box=el('scenario-body');box.replaceChildren(text('p','准备：research_begin → 读取 '+data.scenarios[el('scenario').value].join('、')),text('p','获取：web_search 发现线索 → web_fetch 读原文；必要时读股票已保存来源。'),text('p','分析：容器计算、比较与反证；出现冲突则补查原文，资料不足则收窄结论。'),text('p','核对与交付：检查引用及数字 → brief_publish → 当前对话报告卡。失败保留记录，不伪造交付。'))}el('scenario').onchange=scenario;scenario();
for(const [i,r] of data.runs.entries()){const o=text('option',r.scope+' · '+r.id.slice(0,8));o.value=i;el('run').append(o)}
function replay(){const box=el('run-body');box.replaceChildren();const r=data.runs[el('run').value];if(!r){box.append(text('p','当前没有实际运行记录。'));return}box.append(text('p','模型：'+r.model+'；阶段：'+r.phase+'；状态：'+r.status),text('p','工作区：'+r.workspace+'；产物：'+r.directory),text('pre','实际读取方法：\n'+(r.methods||[]).join('\n')));const table=document.createElement('table');for(const row of r.trace||[]){const tr=document.createElement('tr');for(const v of [row.phase,row.tool,row.status,row.elapsed+'秒',row.path||row.source_id||''])tr.append(text('td',v));table.append(tr)}const wrap=document.createElement('div');wrap.className='scroll';wrap.append(table);box.append(wrap)}el('run').onchange=replay;replay();</script></html>'''.replace('PAYLOAD', payload)


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
