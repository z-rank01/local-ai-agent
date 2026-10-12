import {useState} from 'react';
import type {ReportBlock} from '../lib/reportDocument';
import {Collapse} from './Collapse';
import {Markdown} from './Markdown';
import {Icon} from './Icon';

function ReferenceBlock({block, onSourceReference}: {block: ReportBlock; onSourceReference?: (alias: string) => void}): JSX.Element {
  const [open, setOpen] = useState(false);
  return <section className={`report-reference${block.technical ? ' report-technical' : ''}`} data-open={open} data-report-block={block.id}>
    <button type="button" className="report-reference-head" aria-expanded={open} onClick={() => setOpen(!open)}>
      <span>{block.title}</span><Icon name="i-chev-down" className="ico-sm" />
    </button>
    <Collapse open={open} className="report-reference-body">
      <div className="report-reference-content"><Markdown content={block.content} compactHeadings onSourceReference={onSourceReference} /></div>
    </Collapse>
  </section>;
}

export function ReportDocument({blocks, onSourceReference}: {blocks: ReportBlock[]; onSourceReference?: (alias: string) => void}): JSX.Element {
  return <>{blocks.map(block => block.kind === 'disclosure'
    ? <ReferenceBlock key={block.id} block={block} onSourceReference={onSourceReference} />
    : <section key={block.id} data-report-block={block.id}><Markdown content={block.content} onSourceReference={onSourceReference} /></section>)}</>;
}
