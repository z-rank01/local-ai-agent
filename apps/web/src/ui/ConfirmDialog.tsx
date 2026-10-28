import {useCallback, useEffect, useId, useRef, useState} from 'react';
import type {ReactNode} from 'react';
import {settleDialog, usePendingDialog} from '../lib/confirm';
import type {PendingDialog} from '../lib/confirm';

type ConfirmTask = Extract<PendingDialog, {kind: 'confirm'}>;
type FormTask = Extract<PendingDialog, {kind: 'form'}>;

const FOCUSABLE_SELECTOR = [
  'button:not([disabled])',
  'a[href]',
  'input:not([disabled])',
  'select:not([disabled])',
  'textarea:not([disabled])',
  '[tabindex]:not([tabindex="-1"])',
].join(', ');

function focusableItems(card: HTMLElement): HTMLElement[] {
  return Array.from(card.querySelectorAll<HTMLElement>(FOCUSABLE_SELECTOR));
}

function DialogShell({
  titleId,
  bodyId,
  cardClassName,
  onCancel,
  onEnter,
  children,
}: {
  titleId: string;
  bodyId?: string;
  cardClassName: string;
  onCancel: () => void;
  onEnter?: () => void;
  children: ReactNode;
}): JSX.Element {
  const cardRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const previous = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    const card = cardRef.current;
    if (card) {
      focusableItems(card)[0]?.focus();
    }
    return () => {
      previous?.focus();
    };
  }, []);

  useEffect(() => {
    function handleKeyDown(event: KeyboardEvent) {
      if (event.key === 'Escape') {
        event.preventDefault();
        onCancel();
        return;
      }
      if (
        event.key === 'Enter' &&
        onEnter &&
        !event.isComposing &&
        event.target instanceof HTMLInputElement
      ) {
        event.preventDefault();
        onEnter();
        return;
      }
      if (event.key !== 'Tab') {
        return;
      }
      const card = cardRef.current;
      if (!card) {
        return;
      }
      const items = focusableItems(card);
      if (!items.length) {
        event.preventDefault();
        return;
      }
      const first = items[0];
      const last = items[items.length - 1];
      const active = document.activeElement;
      if (event.shiftKey && (active === first || !card.contains(active))) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && (active === last || !card.contains(active))) {
        event.preventDefault();
        first.focus();
      }
    }
    document.addEventListener('keydown', handleKeyDown);
    return () => document.removeEventListener('keydown', handleKeyDown);
  }, [onCancel, onEnter]);

  return (
    <div
      className="confirm-wrap"
      onMouseDown={(event) => {
        if (event.target === event.currentTarget) {
          onCancel();
        }
      }}
    >
      <div
        className={cardClassName}
        role="alertdialog"
        aria-modal="true"
        aria-labelledby={titleId}
        aria-describedby={bodyId}
        ref={cardRef}
      >
        {children}
      </div>
    </div>
  );
}

function ConfirmHost({task}: {task: ConfirmTask}): JSX.Element {
  const titleId = useId();
  const bodyId = useId();
  const {request} = task;
  const cancel = useCallback(() => settleDialog(task.id, false), [task.id]);

  return (
    <DialogShell
      titleId={titleId}
      bodyId={request.body ? bodyId : undefined}
      cardClassName="confirm-card"
      onCancel={cancel}
    >
      <h3 className="confirm-title" id={titleId}>{request.title}</h3>
      {request.body ? <div className="confirm-body" id={bodyId}>{request.body}</div> : null}
      <div className="confirm-acts">
        <button type="button" className="btn btn-secondary" onClick={cancel}>
          {request.cancelLabel ?? '取消'}
        </button>
        {request.alternateLabel && <button type="button" className="btn btn-secondary" onClick={() => settleDialog(task.id, 'alternate')}>
          {request.alternateLabel}
        </button>}
        <button
          type="button"
          className={request.danger ? 'btn btn-danger' : 'btn btn-primary'}
          onClick={() => settleDialog(task.id, true)}
        >
          {request.okLabel ?? '确认'}
        </button>
      </div>
    </DialogShell>
  );
}

function FormHost({task}: {task: FormTask}): JSX.Element {
  const baseId = useId();
  const titleId = `${baseId}-title`;
  const {request} = task;
  const [values, setValues] = useState<Record<string, string>>(() => {
    const initial: Record<string, string> = {};
    for (const field of request.fields) {
      initial[field.key] = field.value ?? '';
    }
    return initial;
  });
  const [error, setError] = useState<string | null>(null);

  const setValue = useCallback((key: string, value: string) => {
    setValues((current) => ({...current, [key]: value}));
    setError(null);
  }, []);

  const cancel = useCallback(() => settleDialog(task.id, null), [task.id]);

  const submit = useCallback(() => {
    const missing = request.fields.find(
      (field) => field.required && !(values[field.key] ?? '').trim(),
    );
    if (missing) {
      setError(`请填写「${missing.label}」`);
      return;
    }
    const message = request.validate?.(values) ?? null;
    if (message) {
      setError(message);
      return;
    }
    settleDialog(task.id, values);
  }, [request, task.id, values]);

  return (
    <DialogShell
      titleId={titleId}
      cardClassName="confirm-card ws-dialog"
      onCancel={cancel}
      onEnter={submit}
    >
      <h3 className="confirm-title" id={titleId}>{request.title}</h3>
      {request.fields.map((field) => (
        <div className="field" key={field.key}>
          <label className="field-label" htmlFor={`${baseId}-${field.key}`}>{field.label}</label>
          <input
            className="input"
            id={`${baseId}-${field.key}`}
            type="text"
            autoComplete="off"
            spellCheck={false}
            placeholder={field.placeholder}
            value={values[field.key] ?? ''}
            onChange={(event) => setValue(field.key, event.target.value)}
          />
          {field.hint ? <p className="panel-hint">{field.hint}</p> : null}
        </div>
      ))}
      {request.renderExtra?.(values, setValue)}
      {error ? (
        <div className="field">
          <p className="panel-hint" role="alert" style={{color: 'var(--danger-text)'}}>{error}</p>
        </div>
      ) : null}
      <div className="confirm-acts">
        <button type="button" className="btn btn-secondary" onClick={cancel}>
          {request.cancelLabel ?? '取消'}
        </button>
        <button type="button" className="btn btn-primary" onClick={submit}>
          {request.okLabel ?? '确定'}
        </button>
      </div>
    </DialogShell>
  );
}

export function ConfirmDialog(): JSX.Element | null {
  const pending = usePendingDialog();
  if (!pending) {
    return null;
  }
  return pending.kind === 'confirm' ? (
    <ConfirmHost key={pending.id} task={pending} />
  ) : (
    <FormHost key={pending.id} task={pending} />
  );
}
