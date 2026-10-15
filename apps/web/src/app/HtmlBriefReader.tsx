import {useCallback, useEffect, useRef, useState} from 'react';
import type {HtmlBrief} from '../lib/htmlBrief';
import {workspaceRawUrl} from '../api';
import {useLayerFocus} from '../ui/useLayerFocus';

export function HtmlBriefReader({brief, onClose}: {brief: HtmlBrief; onClose: () => void}): JSX.Element {
  const layer = useLayerFocus(true, true);
  const frame = useRef<HTMLIFrameElement>(null);
  const [closing, setClosing] = useState(false);
  const requestClose = useCallback(() => setClosing(true), []);
  useEffect(() => {
    const iframe = frame.current;
    let document: Document | null = null;
    const key = (event: KeyboardEvent) => {
      if (event.key === 'Escape') requestClose();
      if (event.key === 'Tab') {
        const controls = [...(document?.querySelectorAll<HTMLElement>('a[href],summary') ?? [])].filter(node => node.getClientRects().length);
        const first = controls[0], last = controls[controls.length - 1];
        if ((!event.shiftKey && document?.activeElement === last) || (event.shiftKey && document?.activeElement === first)) {
          event.preventDefault();
          const parentControls = layer.current?.querySelectorAll<HTMLElement>('.reader-head a, .reader-head button');
          parentControls?.[event.shiftKey ? parentControls.length - 1 : 0]?.focus();
        }
      }
    };
    const source = (event: MouseEvent) => {
      const element = event.target as Element | null;
      const href = element?.closest('a')?.getAttribute('href');
      if (!href?.startsWith('#')) return;
      const target = document?.getElementById(href.slice(1));
      if (!target) return;
      event.preventDefault();
      let ancestor: Element | null = target;
      while (ancestor) {
        if (ancestor.tagName.toLowerCase() === 'details') ancestor.setAttribute('open', '');
        ancestor = ancestor.parentElement;
      }
      if (target.id === 'references') target.querySelector('details')?.setAttribute('open', '');
      if (document && iframe?.contentWindow) {
        // Do not inherit publication smooth scrolling across the reader frame.
        document.documentElement.style.scrollBehavior = 'auto';
        iframe.contentWindow.scrollTo({top: target.getBoundingClientRect().top + iframe.contentWindow.scrollY});
      }
    };
    const loaded = () => {
      document = iframe?.contentDocument ?? null;
      document?.addEventListener('keydown', key);
      document?.addEventListener('click', source);
    };
    iframe?.addEventListener('load', loaded);
    return () => { iframe?.removeEventListener('load', loaded); document?.removeEventListener('keydown', key); document?.removeEventListener('click', source); };
  }, [requestClose, layer]);
  return <section className={`reader open brief-reader${closing ? ' closing' : ''}`} ref={layer}
    onAnimationEnd={event => { if (closing && event.target === event.currentTarget) onClose(); }}
    onKeyDown={event => { if (event.key === 'Escape') { event.stopPropagation(); requestClose(); } }}
    role="dialog" aria-modal="true" aria-label="简报阅读">
    <div className="reader-head">
      <div className="reader-head-main"><div className="reader-eyebrow">工作区简报 · 独立于原任务报告</div><div className="reader-title">{brief.title}</div></div>
      <a className="reader-btn" href={workspaceRawUrl(brief.path, brief.workspaceId)} download>下载 HTML</a>
      <button className="reader-btn" onClick={requestClose}>关闭</button>
    </div>
    <div className="reader-pages">{brief.meta.length ? brief.meta.join(' · ') : '文件未声明资料时点、版本或生成属性，请核对正文。'}</div>
    <iframe ref={frame} className="brief-frame" title={brief.title} srcDoc={brief.html}
      sandbox="allow-same-origin allow-popups allow-popups-to-escape-sandbox" referrerPolicy="no-referrer" />
  </section>;
}
