import {useSyncExternalStore} from 'react';

export type NoticeAction = {label: string; run: () => void | Promise<void>};
export type Notice = {id: number; text: string; kind: 'ok' | 'error'; action?: NoticeAction};

const DISMISS_MS = 2200;
const ACTION_DISMISS_MS = 4000;

let notice: Notice | null = null;
let dismissTimer: number | null = null;
let seq = 0;

const listeners = new Set<() => void>();

function emit(): void {
  for (const listener of listeners) {
    listener();
  }
}

function dismiss(): void {
  dismissTimer = null;
  notice = null;
  emit();
}

export function notify(text: string, kind: 'ok' | 'error' = 'ok', action?: NoticeAction): void {
  seq += 1;
  notice = {id: seq, text, kind, action};
  if (dismissTimer !== null) {
    window.clearTimeout(dismissTimer);
  }
  dismissTimer = window.setTimeout(dismiss, action ? ACTION_DISMISS_MS : DISMISS_MS);
  emit();
}

export function dismissNotice(): void {
  if (dismissTimer !== null) {
    window.clearTimeout(dismissTimer);
  }
  dismiss();
}

function subscribe(listener: () => void): () => void {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}

function getSnapshot(): Notice | null {
  return notice;
}

export function useNotice(): Notice | null {
  return useSyncExternalStore(subscribe, getSnapshot, getSnapshot);
}
