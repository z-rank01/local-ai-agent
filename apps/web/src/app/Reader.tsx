import {useCallback, useEffect, useRef, useState} from 'react';
import type {ReadingReport} from '../lib/report';
import {fetchReportPage} from '../api';
import {reportPageContent} from '../lib/reportPaging';
import {useLayerFocus} from '../ui/useLayerFocus';
import {Icon} from '../ui/Icon';
import {Markdown} from '../ui/Markdown';

export type ReaderProps = {
  report: ReadingReport | null;
  conversationId?: string;
  onClose: () => void;
  zoomed: boolean;
  onToggleZoom: () => void;
  onPageRead?: (report: ReadingReport) => void;
};
type ReaderLayerProps = Omit<ReaderProps, 'report'> & {report: ReadingReport};

function ReaderLayer({report, conversationId, onClose, zoomed, onToggleZoom, onPageRead}: ReaderLayerProps): JSX.Element {
  const layerRef = useLayerFocus(true, true);
  const bodyRef = useRef<HTMLDivElement>(null);
  const requestSequence = useRef(0);
  const [opened, setOpened] = useState(false);
  const [current, setCurrent] = useState(report);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [failedPage, setFailedPage] = useState<number | null>(null);
  const canRead = Boolean(conversationId && report.sourceMessageId);
  const page = current.page ?? 1;
  const total = current.pageCount;

  const loadPage = useCallback(async (target: number) => {
    if (!conversationId || !report.sourceMessageId) return;
    const sequence = ++requestSequence.current;
    setLoading(true); setError(''); setFailedPage(null);
    try {
      const result = await fetchReportPage(conversationId, report.sourceMessageId, target);
      if (sequence !== requestSequence.current) return;
      if (result.page !== target || result.page_count < target || !result.content) {
        throw new Error('报告页回执不一致，已显示的页面保留。');
      }
      const next = {...report, content: result.content, page: result.page, pageCount: result.page_count};
      setCurrent(next); onPageRead?.(next);
      bodyRef.current?.scrollTo({top: 0});
    } catch (cause) {
      if (sequence !== requestSequence.current) return;
      setError(cause instanceof Error ? cause.message : '读取失败，已显示的页面保留。');
      setFailedPage(target);
    } finally {
      if (sequence === requestSequence.current) setLoading(false);
    }
  }, [conversationId, report, onPageRead]);

  useEffect(() => {
    setOpened(true);
    void loadPage(report.page ?? 1);
    return () => { ++requestSequence.current; };
  }, [loadPage, report.page]);

  return (
    <section className={['reader', opened ? 'open' : '', zoomed ? 'zoomed' : ''].filter(Boolean).join(' ')}
      ref={layerRef} role="dialog" aria-modal="true" aria-label="报告阅读">
      <div className="reader-head">
        <div className="reader-head-main">
          <div className="reader-eyebrow">本地报告</div>
          <div className="reader-title">{report.title}</div>
        </div>
        <button type="button" className="reader-btn" aria-pressed={zoomed} onClick={onToggleZoom}>
          <Icon name={zoomed ? 'i-shrink' : 'i-expand'} className="ico-sm" />
          <span>{zoomed ? '还原' : '放大'}</span>
        </button>
        <button type="button" className="reader-btn" onClick={onClose}>
          <Icon name="i-x" className="ico-sm" />关闭
        </button>
      </div>
      {canRead ? <nav className="reader-pages" aria-label="报告分页">
        <button className="reader-btn" disabled={loading || page <= 1} onClick={() => void loadPage(1)}>第一页</button>
        <button className="reader-btn" disabled={loading || page <= 1} onClick={() => void loadPage(page-1)}>上一页</button>
        <span aria-live="polite">{loading ? '读取中…' : ''}第 {page} / {total ?? '待核对'} 页</span>
        <button className="reader-btn" disabled={loading || !total || page >= total} onClick={() => void loadPage(page+1)}>下一页</button>
        <button className="reader-btn" disabled={loading || !total || page >= total} onClick={() => total && void loadPage(total)}>末页</button>
      </nav> : null}
      <div className="reader-body" ref={bodyRef} aria-busy={loading}>
        <div className="reader-col">
          {error ? <div className="reader-feedback" role="alert">
            {error}
            <button className="reader-btn" disabled={loading} onClick={() => failedPage && void loadPage(failedPage)}>重试读取</button>
          </div> : null}
          {!canRead && report.pageCount && report.pageCount > 1 ?
            <p className="reader-feedback">这份历史记录缺少翻页引用，当前展示已保存的第 {page} 页。</p> : null}
          <Markdown key={page} content={reportPageContent(current.content)} />
        </div>
      </div>
    </section>
  );
}

export function Reader(props: ReaderProps): JSX.Element | null {
  if (!props.report) return null;
  return <ReaderLayer key={props.report.sourceMessageId ?? props.report.title} {...props} report={props.report} />;
}
