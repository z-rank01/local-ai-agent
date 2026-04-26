import React, {useState} from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import rehypeKatex from 'rehype-katex';
import rehypeHighlight from 'rehype-highlight';
import 'katex/dist/katex.min.css';
import 'highlight.js/styles/github-dark.css';

type MarkdownMessageProps = {
  content: string;
};

function getCodeText(children: React.ReactNode): string {
  return React.Children.toArray(children)
    .map((child) => (typeof child === 'string' ? child : ''))
    .join('')
    .replace(/\n$/, '');
}

function CodeBlock({className, children, inline, node, ...props}: any) {
  const [copied, setCopied] = useState(false);
  const code = getCodeText(children);
  const language = /language-(\w+)/.exec(className ?? '')?.[1] ?? '';
  const isInline = inline ?? (!className && node?.position?.start?.line === node?.position?.end?.line);

  if (isInline) {
    return (
      <code className="inline-code" {...props}>
        {children}
      </code>
    );
  }

  const copy = async () => {
    await navigator.clipboard.writeText(code);
    setCopied(true);
    window.setTimeout(() => setCopied(false), 1200);
  };

  return (
    <figure className="code-card">
      <figcaption>
        <span>{language || 'code'}</span>
        <button type="button" onClick={copy}>{copied ? '已复制' : '复制'}</button>
      </figcaption>
      <pre>
        <code className={className} {...props}>{children}</code>
      </pre>
    </figure>
  );
}

export function MarkdownMessage({content}: MarkdownMessageProps) {
  return (
    <ReactMarkdown
      remarkPlugins={[remarkGfm]}
      rehypePlugins={[rehypeKatex, rehypeHighlight]}
      components={{
        code: CodeBlock,
        a: ({children, href}) => (
          <a href={href} target="_blank" rel="noreferrer">
            {children}
          </a>
        ),
      }}
    >
      {content}
    </ReactMarkdown>
  );
}
