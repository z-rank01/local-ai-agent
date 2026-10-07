import {useId, useLayoutEffect, useRef, useState} from 'react';
import type {KeyboardEvent as ReactKeyboardEvent} from 'react';
import {Icon} from './Icon';
import {Popover} from './Popover';

export type DropdownOption = {
  value: string;
  label: string;
  hint?: string;
};

export type DropdownProps = {
  value: string;
  options: DropdownOption[];
  onChange: (value: string) => void;
  disabled?: boolean;
  ariaLabel: string;
  placeholder?: string;
};

export function Dropdown({
  value,
  options,
  onChange,
  disabled = false,
  ariaLabel,
  placeholder = '请选择…',
}: DropdownProps): JSX.Element {
  const [open, setOpen] = useState(false);
  const triggerRef = useRef<HTMLButtonElement | null>(null);
  const optionRefs = useRef<(HTMLButtonElement | null)[]>([]);
  const listId = useId();
  const selected = options.find((option) => option.value === value);

  /* 打开时把焦点落到当前选中项（无选中则首项），方向键从它继续走 */
  useLayoutEffect(() => {
    if (!open) return;
    const index = options.findIndex((option) => option.value === value);
    optionRefs.current[index < 0 ? 0 : index]?.focus();
  }, [open]);

  const pick = (next: string) => {
    setOpen(false);
    triggerRef.current?.focus({preventScroll: true});
    onChange(next);
  };

  const goTo = (index: number) => {
    const total = options.length;
    if (!total) return;
    optionRefs.current[((index % total) + total) % total]?.focus();
  };

  const onListKeyDown = (event: ReactKeyboardEvent<HTMLDivElement>) => {
    const total = options.length;
    if (!total) return;
    const current = optionRefs.current.findIndex((node) => node === document.activeElement);
    if (event.key === 'ArrowDown') {
      event.preventDefault();
      goTo(current < 0 ? 0 : current + 1);
    } else if (event.key === 'ArrowUp') {
      event.preventDefault();
      goTo(current < 0 ? total - 1 : current - 1);
    } else if (event.key === 'Home') {
      event.preventDefault();
      goTo(0);
    } else if (event.key === 'End') {
      event.preventDefault();
      goTo(total - 1);
    }
  };

  return (
    <div className="dropdown">
      <button
        ref={triggerRef}
        type="button"
        className="dropdown-trigger"
        aria-haspopup="listbox"
        aria-expanded={open}
        aria-controls={listId}
        aria-label={ariaLabel}
        disabled={disabled}
        onClick={() => setOpen((current) => !current)}
      >
        <span className="dropdown-value">{selected ? selected.label : placeholder}</span>
        <Icon name="i-chev-down" className="ico-sm" />
      </button>
      <Popover
        anchor={triggerRef}
        open={open}
        onClose={() => setOpen(false)}
        className="pop dropdown-menu"
      >
        <div id={listId} role="listbox" aria-label={ariaLabel} onKeyDown={onListKeyDown}>
          {options.map((option, index) => (
            <button
              key={option.value}
              ref={(node) => {
                optionRefs.current[index] = node;
              }}
              type="button"
              className="pop-item"
              role="option"
              aria-selected={option.value === value}
              onClick={() => pick(option.value)}
            >
              <span>{option.label}</span>
              {option.hint ? <span className="picker-opt-sub">{option.hint}</span> : null}
              <Icon name="i-check" className="ico-sm" />
            </button>
          ))}
        </div>
      </Popover>
    </div>
  );
}
