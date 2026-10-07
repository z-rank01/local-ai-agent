import {useEffect, useState} from 'react';
import type {ReadingReport} from '../lib/report';
import {useLayerFocus} from '../ui/useLayerFocus';
import {Icon} from '../ui/Icon';
import {Markdown} from '../ui/Markdown';

export type ReaderProps = {
  report: ReadingReport | null;
  onClose: () => void;
  zoomed: boolean;
  onToggleZoom: () => void;
};

type ReaderLayerProps = {
  report: ReadingReport;
  onClose: () => void;
  zoomed: boolean;
  onToggleZoom: () => void;
};

/* 报告沉浸阅读（石墨黑）：只负责展示，放大与否由 App 决定并传入 zoomed。 */
function ReaderLayer({report, onClose, zoomed, onToggleZoom}: ReaderLayerProps): JSX.Element {
  const layerRef = useLayerFocus(true, true);
  const [opened, setOpened] = useState(false);

  /* 这个浮层是按报告条件挂载的，直接带 .open 渲染就没有入场过渡：
     先渲染隐藏态，挂载后再加类，让 .reader 的透明度/缩放过渡能播。 */
  useEffect(() => {
    setOpened(true);
  }, []);

  return (
    <section
      className={['reader', opened ? 'open' : '', zoomed ? 'zoomed' : ''].filter(Boolean).join(' ')}
      ref={layerRef}
      role="dialog"
      aria-modal="true"
      aria-label="报告阅读"
    >
      <div className="reader-head">
        <div className="reader-head-main">
          <div className="reader-eyebrow">本地报告</div>
          <div className="reader-title">{report.title}</div>
        </div>
        <button
          type="button"
          className="reader-btn"
          aria-pressed={zoomed}
          onClick={onToggleZoom}
        >
          <Icon name={zoomed ? 'i-shrink' : 'i-expand'} className="ico-sm" />
          <span>{zoomed ? '还原' : '放大'}</span>
        </button>
        <button type="button" className="reader-btn" onClick={onClose}>
          <Icon name="i-x" className="ico-sm" />
          关闭
        </button>
      </div>
      <div className="reader-body">
        <div className="reader-col">
          <Markdown content={report.content} />
        </div>
      </div>
    </section>
  );
}

export function Reader(props: ReaderProps): JSX.Element | null {
  if (!props.report) {
    return null;
  }
  return (
    <ReaderLayer
      report={props.report}
      onClose={props.onClose}
      zoomed={props.zoomed}
      onToggleZoom={props.onToggleZoom}
    />
  );
}
