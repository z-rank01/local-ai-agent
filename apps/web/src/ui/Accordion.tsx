import type {ReactNode} from 'react';
import {Collapse} from './Collapse';
import {Icon} from './Icon';

export type AccordionProps = {
  open: boolean;
  onToggle: () => void;
  head: ReactNode;
  meta?: ReactNode;
  className?: string;
  children: ReactNode;
};

export function Accordion({
  open,
  onToggle,
  head,
  meta,
  className,
  children,
}: AccordionProps): JSX.Element {
  return (
    <div className={className ? `acc ${className}` : 'acc'} data-open={open ? 'true' : 'false'}>
      <button type="button" className="acc-head" aria-expanded={open} onClick={onToggle}>
        {head}
        <span className="acc-meta">{meta}</span>
        <Icon name="i-chev-down" className="ico-sm" />
      </button>
      <Collapse open={open} className="acc-body">
        <div className="acc-content">{children}</div>
      </Collapse>
    </div>
  );
}
