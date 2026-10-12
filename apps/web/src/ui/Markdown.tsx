import {useWorkspaceId} from '../lib/workspace';
import {workspaceRawUrl} from '../api';
import {copyText} from '../lib/format';
import {Icon} from './Icon';
import {splitReportDisclosures} from '../lib/reportDisclosure';
import React, {useState} from 'react';
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

const components: Components = {
  pre: CodeBlock,
  code: InlineCode,
  a: Anchor,
  table: Table,
};

export function Markdown({content, className}: {content: string; className?: string}): JSX.Element {
  const render = (text: string): JSX.Element => (
    <ReactMarkdown remarkPlugins={[remarkGfm]} rehypePlugins={[rehypeKatex, rehypeHighlight]}
      components={components}>{text}</ReactMarkdown>
  );
  return (
    <div className={['md', className].filter(Boolean).join(' ')}>
      {splitReportDisclosures(content).map((part, index) => part.kind === 'disclosure'
        ? <details className="md-disclosure" key={index}>
            <summary>{part.title}</summary><div className="md">{render(part.content)}</div>
          </details>
        : <React.Fragment key={index}>{render(part.content)}</React.Fragment>)}
    </div>
  );
}
