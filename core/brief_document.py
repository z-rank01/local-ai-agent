"""Static reading document for a completed brief; no factual rewriting."""
from html import escape
from pathlib import Path
from urllib.parse import urlsplit
from datetime import datetime
from decimal import Decimal
from hashlib import sha256
import json
import re

from markdown_it import MarkdownIt


def _scale_charts(material, digest):
    if material is None:
        return ''
    packet = material['packet']
    encoded = json.dumps(packet, ensure_ascii=False, sort_keys=True, allow_nan=False)
    if sha256(encoded.encode()).hexdigest() != digest:
        raise ValueError('图表材料与正文材料指纹不一致')
    from .stock_calculation_review import validate_calculations
    charts = []
    for task in packet['tasks']:
        for calc in validate_calculations(task['program_calculations']):
            baseline = Decimal(calc['annual_revenue_yuan'])
            amount = Decimal(calc['amount_yuan'])
            period = calc['annual_period'][:4]
            series = [('项目总额与年度营收的规模参照', amount, '中标总额（跨期）', '跨期总额与年度基数比较，不是单年新增收入。')]
            mean = calc.get('service_mean')
            if mean:
                years = Decimal(mean['service_years'])
                average = Decimal(mean['mean_amount_yuan'])
                if years <= 0 or average != amount / years:
                    raise ValueError('算术平均与原总额不一致')
                series.append(('算术平均的规模参照', average, '运营期限算术平均', '平均数不要求各年均匀，不代表任何年度实际确认收入。'))
            for heading, value, label, note in series:
                top = max(baseline, value)
                svg = f'<svg viewBox="0 0 520 170" role="img" aria-label="{heading}，单位万元">'
                for i, (name, number) in enumerate([(period+'年营业收入', baseline), (label, value)]):
                    y = 44 + i * 76
                    width = float(number / top * 340)
                    svg += (f'<text x="8" y="{y-14}">{escape(name)}</text>'
                            f'<rect x="8" y="{y}" width="{width:.3f}" height="18" fill="{["#a3afa7", "#8f6a1d"][i]}"/>'
                            f'<text x="510" y="{y+15}" text-anchor="end">{number/Decimal(10000):,.2f}</text>')
                svg += '</svg>'
                charts.append(f'<figure><figcaption>{heading}</figcaption><p class="unit">单位：万元；独立零起点刻度。</p><div class="chart-panel">{svg}</div><p class="figure-note">{note} 数据来自同一份已绑定程序回执；<a href="#references">查看原值与来源</a>。</p></figure>')
    return '<section id="charts"><h2>关键规模图示</h2>'+''.join(charts)+'</section>' if charts else ''


def render_document(markdown, *, title, generated_at, model, material_sha256,
                    edition='v1', material=None, as_of='已有保存资料，未更新至生成日',
                    scope_label='研究简报', material_note=None,
                    authorship='模型生成 · 程序排版'):
    """Render received prose as-is with explicit saved-material provenance.

    No remote images, executable HTML or scripts. This function neither approves
    content nor writes a workspace file; callers keep the original model text.
    """
    if not isinstance(markdown, str) or not markdown.strip():
        raise ValueError('简报正文为空')
    md = MarkdownIt('commonmark', {'html': False}).enable('table')
    md.validateLink = lambda url: (url.startswith('#') or
        (urlsplit(url).scheme == 'https' and bool(urlsplit(url).hostname)
         and not urlsplit(url).username and not urlsplit(url).password))
    md.renderer.rules['image'] = lambda tokens, idx, options, env: escape(tokens[idx].content)
    lines = markdown.splitlines()
    # Move an identical document title into the page header, without changing prose.
    if lines and lines[0] == '# ' + title:
        lines = lines[1:]
    body = md.render('\n'.join(lines))
    # Layout roles only: preserve every word and do not infer an investment stance.
    body = re.sub(r'(<h2>结论[：:].*?</h2>.*?)(?=<h2>|$)',
                  r'<section class="verdict">\1</section>', body, count=1, flags=re.S)
    reference = re.search(r'<h2>(?:来源与精确数值|精确原值与来源|论证与参考)</h2>', body)
    refs = ''
    if reference:
        refs = ('<section class="references" id="references"><h2>论证与参考</h2>'
                '<details><summary>展开来源与精确数值</summary>'+body[reference.end():]+'</details></section>')
        body = body[:reference.start()]
    else:
        refs = '<section class="references" id="references"><h2>论证与参考</h2><p>来源请核对正文引用；未新增来源声明。</p></section>'
    body += _scale_charts(material, material_sha256) + refs
    display_time = datetime.fromisoformat(generated_at).strftime('%Y-%m-%d %H:%M')
    if material_note is None:
        material_note = '材料从已有公开研究回执整理，未进行新资料采集。生成日期不是资料截止日期；原研究状态不变。'
    css = (Path(__file__).parent / 'templates' / 'brief.css').read_text(encoding='utf-8')
    return f'''<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="material-sha256" content="{escape(material_sha256, quote=True)}">
<meta name="brief-edition" content="{escape(edition, quote=True)}">
<meta name="brief-as-of" content="{escape(as_of, quote=True)}">
<meta name="brief-scope" content="{escape(scope_label, quote=True)}">
<meta name="brief-authorship" content="{escape(authorship, quote=True)}">
<meta name="brief-research-status" content="已有资料写作试点；原研究状态不变">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'; base-uri 'none'; form-action 'none'">
<title>{escape(title)}</title><style>{css}</style></head>
<body class="stance-neutral"><main class="sheet"><header>
<div class="eyebrow">RESEARCH BRIEF / 已保存资料写作</div><h1>{escape(title)}</h1>
<div class="meta"><span class="badge">{escape(edition)}</span><span>{escape(model)} · {escape(authorship)}</span><span>生成时间：{display_time}</span></div>
<p class="unit">报告范围：{escape(scope_label)} · 资料时点：{escape(as_of)}</p>
<p class="unit">{escape(material_note)}</p>
<nav class="reading"><a href="#references">论证与参考</a></nav>
</header><article class="body">{body}</article>
<footer>已有资料写作试点 · 原始模型输出另行保留；排版完成不代表事实或推断自动认证。</footer>
</main></body></html>'''
