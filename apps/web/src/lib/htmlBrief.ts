import type {WorkspaceFilePreview} from '../types';

export type HtmlBrief = {html: string; path: string; workspaceId: string; title: string; meta: string[]};

export function isWorkspaceHtml(path: string): boolean {
  return /^\/(workspace|roots\/[^/]+)\/.+\.html?$/i.test(path) && !/[?#\\]/.test(path)
    && !path.split('/').some(part => part === '..' || part === '.');
}

// The workspace API enforces access. This projection is only for inert reading;
// the original HTML remains available through the existing download endpoint.
export function prepareHtmlBrief(file: WorkspaceFilePreview, workspaceId: string): HtmlBrief {
  if (!isWorkspaceHtml(file.path) || file.is_binary || file.truncated || !file.content) {
    throw new Error('HTML 未完整读取，请从工作区下载原文件。');
  }
  // Template content remains inert even while parsing image/resource attributes.
  const template = document.createElement('template'); template.innerHTML = file.content;
  const root = template.content;
  const metadata = (name: string) => root.querySelector(`meta[name="${name}"]`)?.getAttribute('content')?.trim();
  const meta = [metadata('brief-edition'), metadata('brief-as-of'), metadata('brief-authorship'), metadata('brief-research-status')];
  const title = root.querySelector('title')?.textContent || file.name;
  const allowed = new Set('html head body title style article header main footer section nav div span p h1 h2 h3 h4 h5 h6 strong em b i small sup sub br hr ul ol li dl dt dd table caption thead tbody tfoot tr th td figure figcaption a details summary svg g rect circle line path text tspan polyline polygon ellipse defs clipPath'.toLowerCase().split(' '));
  const attrs = new Set('id class style lang dir role aria-label aria-labelledby aria-hidden viewbox xmlns x y x1 x2 y1 y2 width height rx ry r cx cy d points fill stroke stroke-width text-anchor font-size transform preserveaspectratio colspan rowspan scope open'.split(' '));
  for (const node of [...root.querySelectorAll('*')]) {
    if (!allowed.has(node.tagName.toLowerCase())) { node.remove(); continue; }
    for (const attr of [...node.attributes]) {
      if (node.tagName.toLowerCase() === 'a' && attr.name === 'href') continue;
      if (!attrs.has(attr.name.toLowerCase())) node.removeAttribute(attr.name);
    }
    if (node.tagName.toLowerCase() === 'a') {
      const href = node.getAttribute('href') ?? '';
      if (/^https?:\/\//i.test(href)) {
        node.setAttribute('target', '_blank'); node.setAttribute('rel', 'noopener noreferrer');
      } else if (!/^#[\w-]+$/.test(href)) node.removeAttribute('href');
    }
  }
  // In-reader downloads use the parent control, never relative srcdoc URLs.
  root.querySelectorAll('.downloads').forEach(node => node.remove());
  const doc = document.implementation.createHTMLDocument(title);
  doc.documentElement.lang = 'zh-CN';
  const references = root.querySelector('section.references');
  // Preserve a document's own disclosure and its open state; avoid two nested
  // toggles that make the reference link appear to open an empty section.
  if (references && !references.querySelector('details')) {
    const details = doc.createElement('details');
    const summary = doc.createElement('summary'); summary.textContent = '展开论证与参考';
    details.append(summary);
    while (references.firstChild) details.append(references.firstChild);
    references.append(details);
  }
  const csp = doc.createElement('meta'); csp.httpEquiv = 'Content-Security-Policy';
  csp.content = "default-src 'none'; script-src 'none'; style-src 'unsafe-inline'; img-src 'none'; font-src 'none'; connect-src 'none'; form-action 'none'; base-uri 'none'";
  doc.head.prepend(csp);
  doc.body.append(root);
  return {html: '<!doctype html>' + doc.documentElement.outerHTML, path: file.path, workspaceId, title,
    meta: meta.filter((item): item is string => Boolean(item)).map(item => item.slice(0, 160))};
}
