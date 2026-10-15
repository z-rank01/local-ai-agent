import {Icon} from './Icon';

/** Presentation only: callers own file permissions, metadata and opening behavior. */
export type ArtifactCardProps = {
  title: string;
  badge: string;
  meta?: string[];
  actionLabel: string;
  onOpen: () => void;
};

export function ArtifactCard({title, badge, meta = [], actionLabel, onOpen}: ArtifactCardProps): JSX.Element {
  return (
    <section className="report-card" aria-label={badge}>
      <span className="report-glyph" aria-hidden="true"><Icon name="i-doc" className="ico-lg" /></span>
      <div className="report-main">
        <span className="report-badge">{badge}</span>
        <div className="report-title">{title}</div>
        {meta.length ? <div className="report-meta">
          {meta.map(item => <span key={item} className="chip chip-quiet chip-mono">{item}</span>)}
        </div> : null}
      </div>
      <button type="button" className="btn btn-secondary" onClick={onOpen}>{actionLabel}</button>
    </section>
  );
}
