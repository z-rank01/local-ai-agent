import {useEffect, useId, useRef, useState} from 'react';
import type {ReactNode} from 'react';
import {readString, STORAGE_KEYS, writeString} from '../lib/storage';
import {Collapse} from './Collapse';
import {Icon} from './Icon';

export type PanelProps = {
  id: string;
  title: string;
  meta?: ReactNode;
  actions?: ReactNode;
  defaultOpen?: boolean;
  forceOpenSignal?: number;
  children: ReactNode;
  beforeToggle?: () => Promise<boolean>;
};

/* Sheet 内小节：折叠只切 CSS 轨道（grid-template-rows 0fr→1fr），
   子元素始终挂载，面板里的轮询/表格状态不会因为收起而丢失。 */
export function Panel({
  id,
  title,
  meta,
  actions,
  defaultOpen = true,
  forceOpenSignal,
  children,
  beforeToggle,
}: PanelProps): JSX.Element {
  const storageKey = STORAGE_KEYS.panel(id);
  const bodyId = useId();
  const [open, setOpen] = useState(() => {
    const raw = readString(storageKey);
    if (raw === 'true') return true;
    if (raw === 'false') return false;
    return defaultOpen;
  });
  const signalRef = useRef(forceOpenSignal);

  useEffect(() => {
    if (forceOpenSignal === undefined || forceOpenSignal === signalRef.current) return;
    signalRef.current = forceOpenSignal;
    setOpen(true);
    writeString(storageKey, 'true');
  }, [forceOpenSignal, storageKey]);

  const toggle = async () => {
    if (beforeToggle && !await beforeToggle()) return;
    const next = !open;
    setOpen(next);
    writeString(storageKey, next ? 'true' : 'false');
  };

  return (
    <section className="panel" data-collapsed={open ? 'false' : 'true'}>
      <button
        type="button"
        className="panel-head"
        aria-expanded={open}
        aria-controls={bodyId}
        onClick={toggle}
      >
        <span className="panel-title">{title}</span>
        <span className="panel-meta">{meta}</span>
        {actions == null ? null : (
          <span className="row-lead" onClick={(event) => event.stopPropagation()}>
            {actions}
          </span>
        )}
        <Icon name="i-chev-down" className="ico-sm panel-chev" />
      </button>
      <Collapse open={open} className="panel-body">
        <div className="panel-content" id={bodyId}>
          {children}
        </div>
      </Collapse>
    </section>
  );
}
