import {useEffect, useRef} from 'react';

const selector = 'button:not([disabled]),a[href],input:not([disabled]),textarea:not([disabled]),select:not([disabled]),iframe,[tabindex="0"]';

export function useLayerFocus(open: boolean, modal: boolean) {
  const ref = useRef<HTMLElement>(null);
  useEffect(() => {
    const root = ref.current;
    if (!root) return;
    root.inert = !open;
    if (!open) return;
    const previous = document.activeElement as HTMLElement | null;
    const controls = () => [...root.querySelectorAll<HTMLElement>(selector)].filter(node => node.getClientRects().length);
    controls()[0]?.focus({preventScroll: true});
    const trap = (event: KeyboardEvent) => {
      if (!modal || event.key !== 'Tab' || document.activeElement?.closest('.pop')) return;
      const items = controls();
      const first = items[0], last = items[items.length - 1];
      if (!first) { event.preventDefault(); return; }
      if (event.shiftKey && (document.activeElement === first || !root.contains(document.activeElement))) {
        event.preventDefault(); last?.focus();
      } else if (!event.shiftKey && (document.activeElement === last || !root.contains(document.activeElement))) {
        event.preventDefault(); first.focus();
      }
    };
    document.addEventListener('keydown', trap);
    return () => {
      document.removeEventListener('keydown', trap);
      if (previous?.isConnected && !previous.closest('[inert]')) previous.focus({preventScroll: true});
    };
  }, [open, modal]);
  return ref;
}
