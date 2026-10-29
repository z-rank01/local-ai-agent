import type {Ref} from 'react';

export type SwitchProps = {
  checked: boolean;
  onChange: (next: boolean) => void;
  disabled?: boolean;
  label: string;
  buttonRef?: Ref<HTMLButtonElement>;
};

export function Switch({checked, onChange, disabled = false, label, buttonRef}: SwitchProps): JSX.Element {
  return (
    <button
      ref={buttonRef}
      type="button"
      className={checked ? 'switch on' : 'switch'}
      role="switch"
      aria-checked={checked}
      aria-label={label}
      disabled={disabled}
      onClick={() => onChange(!checked)}
    />
  );
}
