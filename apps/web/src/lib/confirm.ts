import {useSyncExternalStore} from 'react';
import type {ReactNode} from 'react';

export type ConfirmRequest = {
  title: string;
  body?: ReactNode;
  okLabel?: string;
  cancelLabel?: string;
  danger?: boolean;
};

export type FormField = {
  key: string;
  label: string;
  placeholder?: string;
  value?: string;
  required?: boolean;
  hint?: string;
};

export type FormRequest = {
  title: string;
  okLabel?: string;
  cancelLabel?: string;
  fields: FormField[];
  validate?: (values: Record<string, string>) => string | null;
  renderExtra?: (
    values: Record<string, string>,
    setValue: (key: string, value: string) => void,
  ) => ReactNode;
};

export type PendingDialog =
  | {id: number; kind: 'confirm'; request: ConfirmRequest}
  | {id: number; kind: 'form'; request: FormRequest};

type DialogResult = boolean | Record<string, string> | null;

type Task = PendingDialog & {settle: (value: DialogResult) => void};

let seq = 0;
const queue: Task[] = [];
const listeners = new Set<() => void>();

function emit(): void {
  for (const listener of listeners) {
    listener();
  }
}

export function askConfirm(request: ConfirmRequest): Promise<boolean> {
  return new Promise<boolean>((resolve) => {
    queue.push({
      id: ++seq,
      kind: 'confirm',
      request,
      settle: (value) => resolve(value === true),
    });
    emit();
  });
}

export function askForm(request: FormRequest): Promise<Record<string, string> | null> {
  return new Promise((resolve) => {
    queue.push({
      id: ++seq,
      kind: 'form',
      request,
      settle: (value) => resolve(typeof value === 'object' ? value : null),
    });
    emit();
  });
}

export function settleDialog(id: number, value: DialogResult): void {
  const task = queue[0];
  if (!task || task.id !== id) {
    return;
  }
  queue.shift();
  task.settle(value);
  emit();
}

function subscribe(listener: () => void): () => void {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}

function getSnapshot(): PendingDialog | null {
  return queue[0] ?? null;
}

export function usePendingDialog(): PendingDialog | null {
  return useSyncExternalStore(subscribe, getSnapshot, getSnapshot);
}
