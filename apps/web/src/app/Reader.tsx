import {useCallback, useEffect, useRef, useState} from 'react';
import type {ReadingReport} from '../lib/report';
import {ApiError, fetchReportPage} from '../api';
import {reportPageContent} from '../lib/reportPaging';
import {checkedReportPage, type ReportPage, type ReportSection} from '../lib/reportDocument';
import {useLayerFocus} from '../ui/useLayerFocus';
import {Icon} from '../ui/Icon';
import {Markdown} from '../ui/Markdown';
import {ReportDocument} from '../ui/ReportDocument';

export type ReaderProps = {report: ReadingReport | null; conversationId?: string; onClose: () => void;
  zoomed: boolean; onToggleZoom: () => void; onPageRead?: (report: ReadingReport) => void};
type ReaderLayerProps = Omit<ReaderProps, 'report'> & {report: ReadingReport};
type Destination = {page: number; section: ReportSection};

function ReaderLayer({report, conversationId, onClose, zoomed, onToggleZoom, onPageRead}: ReaderLayerProps): JSX.Element {
  const layerRef = useLayerFocus(true, true);
  const bodyRef = useRef<HTMLDivElement>(null);
  const sequence = useRef(0);
  const revision = useRef(report.readerDocument?.revision);
  const positions = useRef<Partial<Record<ReportSection, ReportPage>>>({...report.readerPositions});
  const jump = useRef<string | null>(null);
  const canRead = Boolean(conversationId && report.sourceMessageId);
  const [opened, setOpened] = useState(false);
  const [current, setCurrent] = useState<ReportPage>(report.readerDocument ?? {
    content: report.content, page: report.page ?? 1, page_count: report.pageCount ?? 1});
  const [loading, setLoading] = useState(canRead);
  const [error, setError] = useState('');
  const [updated, setUpdated] = useState(false);
  const [failed, setFailed] = useState<Destination | null>(null);
  const section = current.section ?? 'body';
  const modern = current.layout === 'research-reader-v2';

  const load = useCallback(async (target: number, view: ReportSection, fresh = false, alias?: string) => {
    if (!conversationId || !report.sourceMessageId) return;
    const id = ++sequence.current;
    setLoading(true); setError(''); setFailed(null); setUpdated(false);
    if (fresh) { revision.current = undefined; positions.current = {}; }
    try {
      const value = checkedReportPage(await fetchReportPage(conversationId, report.sourceMessageId, target, view, revision.current), target, view);
      if (id !== sequence.current) return;
      revision.current = value.revision;
      positions.current[view] = value;
      jump.current = alias ?? null;
      setCurrent(value);
      onPageRead?.({...report, title: value.title ?? report.title, content: value.content,
        page: value.page, pageCount: value.page_count, section: view, readerDocument: value,
        readerPositions: {...positions.current}});
    } catch (cause) {
      if (id !== sequence.current) return;
      setError(cause instanceof Error ? cause.message : '读取失败，当前页面保留。');
      setFailed({page: target, section: view});
      setUpdated(cause instanceof ApiError && cause.code === 'REPORT_UPDATED');
    } finally { if (id === sequence.current) setLoading(false); }
  }, [conversationId, report, onPageRead]);

  useEffect(() => {
    setOpened(true);
    // A legacy card's page number belongs to the old combined document.
    const start = report.readerDocument;
    void load(start?.page ?? 1, start?.section ?? 'body');
    return () => { ++sequence.current; };
  }, [load, report.readerDocument]);
  useEffect(() => {
    bodyRef.current?.scrollTo({top: 0});
    if (jump.current) {
      bodyRef.current?.querySelector(`[data-report-block="source-${jump.current}-part-0"]`)?.scrollIntoView({block: 'start'});
      jump.current = null;
    }
  }, [current]);

  const switchSection = (view: ReportSection) => void load(positions.current[view]?.page ?? 1, view);
  const source = (alias: string) => {
    const page = current.source_pages?.[alias];
    if (page && !loading) void load(page, 'references', false, alias);
  };
  const viewLabel = modern ? section === 'body' ? '正文' : '参考资料' : '整篇';
  return <section className={['reader', opened ? 'open' : '', zoomed ? 'zoomed' : ''].filter(Boolean).join(' ')}
    ref={layerRef} role="dialog" aria-modal="true" aria-label="报告阅读">
    <div className="reader-head">
      <div className="reader-head-main"><div className="reader-eyebrow">本地报告</div><div className="reader-title">{current.title ?? report.title}</div></div>
      <button type="button" className="reader-btn" aria-pressed={zoomed} onClick={onToggleZoom}><Icon name={zoomed ? 'i-shrink' : 'i-expand'} className="ico-sm" />{zoomed ? '还原' : '放大'}</button>
      <button type="button" className="reader-btn" onClick={onClose}><Icon name="i-x" className="ico-sm" />关闭</button>
    </div>
    {modern ? <nav className="reader-sections" aria-label="报告阅读区域">
      <button className="reader-btn" aria-pressed={section === 'body'} disabled={loading} onClick={() => switchSection('body')}>正文</button>
      <button className="reader-btn" aria-pressed={section === 'references'} disabled={loading} onClick={() => switchSection('references')}>参考资料</button>
    </nav> : null}
    {canRead ? <nav className="reader-pages" aria-label="报告分页">
      <button className="reader-btn" disabled={loading || current.page <= 1} onClick={() => void load(1, section)}>第一页</button>
      <button className="reader-btn" disabled={loading || current.page <= 1} onClick={() => void load(current.page-1, section)}>上一页</button>
      <span aria-live="polite">{loading ? '读取中…' : ''}{viewLabel} 第 {current.page} / {current.page_count} 页</span>
      <button className="reader-btn" disabled={loading || current.page >= current.page_count} onClick={() => void load(current.page+1, section)}>下一页</button>
      <button className="reader-btn" disabled={loading || current.page >= current.page_count} onClick={() => void load(current.page_count, section)}>末页</button>
    </nav> : null}
    <div className="reader-body" ref={bodyRef} aria-busy={loading}><div className="reader-col">
      {error ? <div className="reader-feedback" role="alert">{error}
        {updated ? <button className="reader-btn" disabled={loading} onClick={() => void load(1, 'body', true)}>载入最新保存版本</button>
          : <button className="reader-btn" disabled={loading} onClick={() => failed && void load(failed.page, failed.section)}>重试读取</button>}
      </div> : null}
      {loading && !report.readerDocument && !modern ? <p>正在读取已保存报告…</p> :
        modern && current.blocks ? <ReportDocument key={`${current.revision}:${section}:${current.page}`} blocks={current.blocks} onSourceReference={source} />
        : <Markdown content={reportPageContent(current.content)} />}
      {!canRead && report.pageCount && report.pageCount > 1 ? <p className="reader-feedback">这份历史记录缺少翻页引用，当前展示已保存页面。</p> : null}
    </div></div>
  </section>;
}
export function Reader(props: ReaderProps): JSX.Element | null {
  if (!props.report) return null;
  return <ReaderLayer key={props.report.sourceMessageId ?? props.report.title} {...props} report={props.report} />;
}
