import {useCallback, useEffect, useLayoutEffect, useRef} from 'react';
import type {ReactNode, RefObject} from 'react';
import {createPortal} from 'react-dom';

const FOCUSABLE_SELECTOR = [
  'a[href]',
  'button:not([disabled])',
  'input:not([disabled]):not([type="hidden"])',
  'select:not([disabled])',
  'textarea:not([disabled])',
  '[tabindex]:not([tabindex="-1"])',
].join(', ');

export type PopoverProps = {
  anchor: RefObject<HTMLElement>;
  open: boolean;
  onClose: () => void;
  align?: 'start' | 'end';
  width?: number;
  className?: string;
  labelledBy?: string;
  children: ReactNode;
};

export function Popover({
  anchor,
  open,
  onClose,
  align = 'end',
  width,
  className = 'pop',
  labelledBy,
  children,
}: PopoverProps): JSX.Element | null {
  const panelRef = useRef<HTMLDivElement | null>(null);

  /* 浮层 fixed 贴锚点：横向按 align 对齐并夹在视口内，纵向默认在下方、
     空间不足时上翻；量宽时先把 left/width 归零，避免收缩适应被当前位置污染。 */
  const place = useCallback(() => {
    const panel = panelRef.current;
    const host = anchor.current;
    if (!panel || !host) return;
    const rect = host.getBoundingClientRect();
    panel.style.left = '0px';
    panel.style.top = '0px';
    panel.style.width = '';
    const naturalWidth = panel.offsetWidth;
    const panelHeight = panel.offsetHeight;
    const panelWidth = Math.min(window.innerWidth - 16, width ?? Math.max(naturalWidth, Math.round(rect.width)));
    const left = Math.min(
      Math.max(8, align === 'start' ? rect.left : rect.right - panelWidth),
      Math.max(8, window.innerWidth - panelWidth - 8),
    );
    let top = rect.bottom + 6;
    if (top + panelHeight > window.innerHeight - 8 && rect.top - panelHeight - 6 > 8) {
      top = rect.top - panelHeight - 6;
    }
    top = Math.max(8, Math.min(top, Math.max(8, window.innerHeight - panelHeight - 8)));
    panel.style.width = `${panelWidth}px`;
    panel.style.left = `${Math.round(left)}px`;
    panel.style.top = `${Math.round(top)}px`;
  }, [align, anchor, width]);

  useLayoutEffect(() => {
    if (!open) return;
    place();
    const panel = panelRef.current;
    if (!panel) return;
    const first = panel.querySelector<HTMLElement>(FOCUSABLE_SELECTOR);
    (first ?? panel).focus({preventScroll: true});
  }, [open, place]);

  useEffect(() => {
    if (!open) return;
    const onResize = () => place();
    const onScroll = (event: Event) => {
      const panel = panelRef.current;
      if (panel && event.target instanceof Node && panel.contains(event.target)) return;
      onClose();
    };
    const onPointerDown = (event: PointerEvent) => {
      const target = event.target;
      const panel = panelRef.current;
      const host = anchor.current;
      if (!(target instanceof Node)) return;
      if (panel && panel.contains(target)) return;
      if (host && host.contains(target)) return;
      onClose();
    };
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key !== 'Escape') return;
      event.preventDefault();
      event.stopPropagation();
      onClose();
      anchor.current?.focus({preventScroll: true});
    };
    window.addEventListener('resize', onResize);
    document.addEventListener('scroll', onScroll, true);
    document.addEventListener('pointerdown', onPointerDown, true);
    document.addEventListener('keydown', onKeyDown, true);
    return () => {
      window.removeEventListener('resize', onResize);
      document.removeEventListener('scroll', onScroll, true);
      document.removeEventListener('pointerdown', onPointerDown, true);
      document.removeEventListener('keydown', onKeyDown, true);
    };
  }, [anchor, onClose, open, place]);

  if (!open) return null;

  return createPortal(
    <div
      ref={panelRef}
      className={className}
      role="dialog"
      aria-modal="false"
      aria-labelledby={labelledBy}
      tabIndex={-1}
      style={{position: 'fixed'}}
    >
      {children}
    </div>,
    document.body,
  );
}
