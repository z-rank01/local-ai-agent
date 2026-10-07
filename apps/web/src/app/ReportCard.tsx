import type {buildReportCardInfo} from '../lib/report';
import {Icon} from '../ui/Icon';

export type ReportCardProps = {
  info: ReturnType<typeof buildReportCardInfo>;
  onRead: () => void;
};

export function ReportCard({info, onRead}: ReportCardProps): JSX.Element {
  return (
    <section className="report-card" aria-label="本地报告">
      <span className="report-glyph" aria-hidden="true"><Icon name="i-doc" className="ico-lg" /></span>
      <div className="report-main">
        <span className="report-badge">本地报告</span>
        <div className="report-title">{info.title}</div>
        {info.meta.length ? (
          <div className="report-meta">
            {info.meta.map((item) => <span key={item} className="chip chip-quiet chip-mono">{item}</span>)}
          </div>
        ) : null}
      </div>
      <button type="button" className="btn btn-secondary" onClick={onRead}>阅读全文</button>
    </section>
  );
}
