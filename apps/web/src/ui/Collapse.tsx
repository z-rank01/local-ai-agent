import {useLayoutEffect, useRef} from 'react';
import type {ReactNode} from 'react';

const INNER_CLASS: Record<string, string> = {
  'acc-body': 'acc-in',
  'panel-body': 'panel-in',
  'steps-body': 'steps-body-in',
};

export type CollapseProps = {
  open: boolean;
  className?: string;
  children: ReactNode;
};

/* grid-template-rows 折叠容器：外层是网格项（无 padding / border），
   内层 .x-in 负责 overflow:hidden; min-height:0，内容自带的 padding 放在再下一层。
   展开态由宿主组件的直接子状态选择器驱动（.acc[data-open] > .acc-body /
   .panel[data-collapsed="false"] > .panel-body / .trace[data-open] > .steps-body）。 */
export function Collapse({open, className = 'acc-body', children}: CollapseProps): JSX.Element {
  const ref = useRef<HTMLDivElement>(null);
  useLayoutEffect(() => { if (ref.current) ref.current.inert = !open; }, [open]);
  const base = className.split(/\s+/)[0];
  const inner = INNER_CLASS[base] ?? `${base}-in`;
  return (
    <div ref={ref} aria-hidden={!open} className={className} data-open={open ? 'true' : 'false'}>
      <div className={inner}>{children}</div>
    </div>
  );
}
