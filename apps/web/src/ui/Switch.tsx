export type SwitchProps = {
  checked: boolean;
  onChange: (next: boolean) => void;
  disabled?: boolean;
  label: string;
};

export function Switch({checked, onChange, disabled = false, label}: SwitchProps): JSX.Element {
  return (
    <button
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
