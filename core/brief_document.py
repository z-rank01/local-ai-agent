"""Static reading document for a completed brief; no factual rewriting."""
from html import escape
from pathlib import Path
from urllib.parse import urlsplit

from markdown_it import MarkdownIt


def render_document(markdown, *, title, generated_at, model, material_sha256):
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
    css = (Path(__file__).parent / 'templates' / 'brief.css').read_text(encoding='utf-8')
    return f'''<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="material-sha256" content="{escape(material_sha256, quote=True)}">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'; base-uri 'none'; form-action 'none'">
<title>{escape(title)}</title><style>{css}</style></head>
<body class="stance-neutral"><main class="sheet"><header>
<div class="eyebrow">RESEARCH BRIEF / 已保存资料写作</div><h1>{escape(title)}</h1>
<div class="meta">模型生成：{escape(model)} · 生成时间：{escape(generated_at)}</div>
<p class="unit">材料从已有公开研究回执整理，未进行新资料采集。生成日期不是资料截止日期；原研究状态不变。</p>
</header><article class="body">{body}</article>
<footer>已有资料写作试点 · 本阅读版本保留模型原文；排版完成不代表事实或推断自动认证。</footer>
</main></body></html>'''
