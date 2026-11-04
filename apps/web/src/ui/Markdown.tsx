import {BriefReaderContext} from '../lib/briefReader';
import {workspaceDocumentKey, workspaceDocumentKind} from '../lib/workspaceDocument';
import {useWorkspaceId} from '../lib/workspace';
import {workspaceRawUrl} from '../api';
import {copyText} from '../lib/format';
import {Icon} from './Icon';
import {ArtifactCard} from './ArtifactCard';
import {splitReportDisclosures} from '../lib/reportDisclosure';
import React, {useState, useContext} from 'react';
import ReactMarkdown from 'react-markdown';
import type {Components} from 'react-markdown';
import remarkGfm from 'remark-gfm';
import rehypeKatex from 'rehype-katex';
import rehypeHighlight from 'rehype-highlight';
import 'katex/dist/katex.min.css';
import 'highlight.js/styles/github-dark.css';

type ExtraProps = {node?: unknown};

type PreProps = React.HTMLAttributes<HTMLElement> & ExtraProps;
type CodeProps = React.HTMLAttributes<HTMLElement> & ExtraProps;
type AnchorProps = React.AnchorHTMLAttributes<HTMLAnchorElement> & ExtraProps;
type TableProps = React.TableHTMLAttributes<HTMLTableElement> & ExtraProps;

const INLINE_CODE_CLASSES = ['inline', 'inline-code'];

function textOf(node: React.ReactNode): string {
  if (typeof node === 'string' || typeof node === 'number') {
    return String(node);
  }
  if (Array.isArray(node)) {
    return node.map(textOf).join('');
  }
  if (React.isValidElement<{children?: React.ReactNode}>(node)) {
    return textOf(node.props.children);
  }
  return '';
}

function CopyButton({text}: {text: string}): JSX.Element {
  const [copied, setCopied] = useState(false);

  async function copy(): Promise<void> {
    await copyText(text);
    setCopied(true);
    window.setTimeout(() => setCopied(false), 1200);
  }

  return (
    <button type="button" className="code-copy" onClick={copy}>
      <Icon name={copied ? 'i-check' : 'i-copy'} className="ico-sm" />
      {copied ? '已复制' : '复制'}
    </button>
  );
}

function CodeBlock({node, children, ...rest}: PreProps): JSX.Element {
  const child = React.Children.toArray(children)[0];
  if (!React.isValidElement<{className?: string; children?: React.ReactNode}>(child)) {
    return <pre {...rest} className="code-body">{children}</pre>;
  }
  const classes = (child.props.className ?? '').split(/\s+/).filter(Boolean);
  const language = classes.map((name) => /^language-(.+)$/.exec(name)?.[1]).find(Boolean) ?? 'code';
  const codeClassName = classes.filter((name) => !INLINE_CODE_CLASSES.includes(name)).join(' ');
  return (
    <figure className="code-card">
      <figcaption className="code-bar">
        <span>{language}</span>
        <CopyButton text={textOf(child.props.children).replace(/\n$/, '')} />
      </figcaption>
      <pre {...rest} className="code-body">
        <code className={codeClassName || undefined}>{child.props.children}</code>
      </pre>
    </figure>
  );
}

function InlineCode({node, className, children, ...rest}: CodeProps): JSX.Element {
  const classes = [...INLINE_CODE_CLASSES, ...(className ? className.split(/\s+/) : [])];
  return (
    <code {...rest} className={classes.filter(Boolean).join(' ')}>
      {children}
    </code>
  );
}

function messageHref(href: string | undefined, workspaceId: string): string | undefined {
  if (!href?.startsWith('/workspace/') && !href?.startsWith('/roots/')) {
    return href;
  }
  try {
    return workspaceRawUrl(decodeURI(href), workspaceId);
  } catch {
    return workspaceRawUrl(href, workspaceId);
  }
}

function Anchor({node, href, children, ...rest}: AnchorProps): JSX.Element {
  const workspaceId = useWorkspaceId();
  const readBrief = useContext(BriefReaderContext);
  const kind = href ? workspaceDocumentKind(href) : null;
  const path = workspaceDocumentKey(href ?? '');
  if (href && readBrief && kind && path) {
    return <button type="button" className="report-source-link" onClick={() => readBrief(path)}>
      {kind === 'html' ? '阅读简报' : '阅读文档'}
    </button>;
  }
  return (
    <a {...rest} href={messageHref(href, workspaceId)} target="_blank" rel="noreferrer">
      {children}
    </a>
  );
}

function Table({node, children, ...rest}: TableProps): JSX.Element {
  return (
    <div className="md-scroll">
      <table {...rest}>{children}</table>
    </div>
  );
}

type ParagraphNode = {
  type: string;
  value?: string;
  tagName?: string;
  properties?: {href?: unknown};
  children?: ParagraphNode[];
};
type ParagraphProps = React.HTMLAttributes<HTMLParagraphElement> & {
  node?: ParagraphNode;
  suppressedPaths?: ReadonlySet<string>;
};

// A standalone delivery link may have a document prefix. Other inline
// references retain their sentence layout and never participate in deduping.
function Paragraph({node, children, suppressedPaths, ...rest}: ParagraphProps): JSX.Element | null {
  const readBrief = useContext(BriefReaderContext);
  const nodes = node?.children ?? [];
  const deliveryPrefix = /^\s*阅读(?:简报|文档)[:：]\s*$/;
  const prefixed = nodes.length === 2 && nodes[0].type === 'text' && deliveryPrefix.test(nodes[0].value ?? '');
  const link = nodes.length === 1 ? nodes[0] : prefixed ? nodes[1] : undefined;
  if (readBrief && link?.type === 'element' && link.tagName === 'a') {
    const href = link.properties?.href;
    const path = typeof href === 'string' ? workspaceDocumentKey(href) : '';
    const kind = typeof href === 'string' ? workspaceDocumentKind(href) : null;
    if (kind && path) {
      if (suppressedPaths?.has(path)) return null;
      const label = textOf(children).trim().replace(/^阅读(?:简报|文档)[:：]\s*/, '');
      const fallback = kind === 'html' ? 'HTML 简报' : 'Markdown 文档';
      return <ArtifactCard title={label && !['阅读简报', '阅读文档'].includes(label) ? label : fallback}
        badge="工作区文件" meta={[path.split('/').pop() || fallback]}
        actionLabel={kind === 'html' ? '阅读简报' : '阅读文档'} onOpen={() => readBrief(path)} />;
    }
  }
  return <p {...rest}>{children}</p>;
}

const components: Components = {
  pre: CodeBlock,
  code: InlineCode,
  a: Anchor,
  table: Table,
  p: Paragraph,
};

// Each render computes ownership from immutable earlier text, never from shared
// render-time state (React may replay or render sibling components separately).
type DocumentNode = {type: string; value?: string; url?: string; children?: DocumentNode[]};
function dedupeDocumentLinks(this: {parse: (text: string) => DocumentNode}, options: {previous: string[]}) {
  const parse = this.parse.bind(this);
  return (tree: DocumentNode) => {
    const seen = new Set<string>();
    const visit = (node: DocumentNode, remove: boolean) => {
      node.children = node.children?.filter(child => {
        const parts = child.children ?? [];
        const link = parts.length === 1 ? parts[0] : parts.length === 2 && parts[0].type === 'text'
          && /^\s*阅读(?:简报|文档)[:：]\s*$/.test(parts[0].value ?? '') ? parts[1] : undefined;
        if (child.type === 'paragraph' && link?.type === 'link' && workspaceDocumentKind(link.url ?? '')) {
          const path = workspaceDocumentKey(link.url!);
          const duplicate = seen.has(path);
          seen.add(path);
          if (remove && duplicate) return false;
        }
        visit(child, remove);
        return true;
      });
    };
    options.previous.forEach(text => visit(parse(text), false));
    visit(tree, true);
  };
}

export function Markdown({content, className, compactHeadings = false, onSourceReference, suppressedDocumentPaths, precedingDocumentContents = []}: {
  content: string; className?: string; compactHeadings?: boolean;
  onSourceReference?: (alias: string) => void;
  suppressedDocumentPaths?: ReadonlySet<string>;
  precedingDocumentContents?: string[];
}): JSX.Element {
  const reportComponents: Components = {...components,
    ...(compactHeadings ? {h1: ({children}: {children?: React.ReactNode}) => <h3>{children}</h3>,
      h2: ({children}: {children?: React.ReactNode}) => <h4>{children}</h4>} : {}),
    p: props => <Paragraph {...props} suppressedPaths={suppressedDocumentPaths} />,
    a: props => {
      const alias = /^#report-source-(\d+)$/.exec(props.href ?? '')?.[1];
      return alias && onSourceReference
        ? <button type="button" className="report-source-link" onClick={() => onSourceReference(alias)}>{props.children}</button>
        : <Anchor {...props} />;
    },
  };
  const readDocument = useContext(BriefReaderContext);
  const parts = splitReportDisclosures(content);
  const render = (text: string, index: number): JSX.Element => (
    <ReactMarkdown remarkPlugins={[remarkGfm, ...(readDocument ? [[dedupeDocumentLinks, {previous: [...precedingDocumentContents, ...parts.slice(0, index).map(part => part.content)]}]] as [typeof dedupeDocumentLinks, {previous: string[]}][] : [])]} rehypePlugins={[rehypeKatex, rehypeHighlight]}
      components={reportComponents}>{text}</ReactMarkdown>
  );
  return (
    <div className={['md', className].filter(Boolean).join(' ')}>
      {parts.map((part, index) => part.kind === 'disclosure'
        ? <details className="md-disclosure" key={index}>
            <summary>{part.title}</summary><div className="md">{render(part.content, index)}</div>
          </details>
        : <React.Fragment key={index}>{render(part.content, index)}</React.Fragment>)}
    </div>
  );
}
