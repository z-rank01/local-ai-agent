import {useSyncExternalStore} from 'react';

export type Notice = {id: number; text: string; kind: 'ok' | 'error'};

const DISMISS_MS = 2200;

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

export function notify(text: string, kind: 'ok' | 'error' = 'ok'): void {
  seq += 1;
  notice = {id: seq, text, kind};
  if (dismissTimer !== null) {
    window.clearTimeout(dismissTimer);
  }
  dismissTimer = window.setTimeout(dismiss, DISMISS_MS);
  emit();
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
