import React from 'react';
import {Box, Text} from 'ink';
import {marked} from 'marked';

type MarkdownToken = {
  type: string;
  text?: string;
  raw?: string;
  depth?: number;
  ordered?: boolean;
  lang?: string;
  href?: string;
  tokens?: MarkdownToken[];
  items?: Array<{
    text?: string;
    tokens?: MarkdownToken[];
    checked?: boolean;
    task?: boolean;
  }>;
  header?: Array<{text?: string; tokens?: MarkdownToken[]}>;
  rows?: Array<Array<{text?: string; tokens?: MarkdownToken[]}>>;
};

type MarkdownPalette = {
  text: string;
  muted: string;
  accent: string;
  link: string;
  code: string;
  quote: string;
};

type MarkdownMessageProps = {
  content: string;
  palette?: Partial<MarkdownPalette>;
};

const DEFAULT_PALETTE: MarkdownPalette = {
  text: '#f3efe8',
  muted: '#8f98a4',
  accent: '#e7d7c9',
  link: '#a7c7d8',
  code: '#d6c3a5',
  quote: '#99a1ab',
};

const RULE = '────────────────────────────────────────';

function textToken(value: string): MarkdownToken[] {
  return value ? [{type: 'text', text: value}] : [];
}

function flattenInlineTokens(tokens: MarkdownToken[]): string {
  return tokens
    .map((token) => {
      if (token.type === 'link') {
        const label = flattenInlineTokens(token.tokens?.length ? token.tokens : textToken(token.text ?? ''));
        const href = token.href ? ` (${token.href})` : '';
        return `${label}${href}`;
      }

      if (token.tokens?.length) {
        return flattenInlineTokens(token.tokens);
      }

      return token.text ?? '';
    })
    .join('');
}

function flattenBlockTokens(tokens: MarkdownToken[]): string {
  return tokens
    .map((token) => {
      switch (token.type) {
        case 'paragraph':
        case 'heading':
        case 'text':
          return flattenInlineTokens(token.tokens?.length ? token.tokens : textToken(token.text ?? ''));

        case 'code':
          return token.text ?? '';

        case 'list':
          return (token.items ?? [])
            .map((item) => `- ${flattenBlockTokens(item.tokens?.length ? item.tokens : textToken(item.text ?? ''))}`)
            .join('\n');

        case 'blockquote':
          return flattenBlockTokens(token.tokens ?? []);

        case 'table': {
          const header = (token.header ?? []).map((cell) => flattenInlineTokens(cell.tokens?.length ? cell.tokens : textToken(cell.text ?? '')));
          const rows = (token.rows ?? []).map((row) =>
            row.map((cell) => flattenInlineTokens(cell.tokens?.length ? cell.tokens : textToken(cell.text ?? ''))).join(' | '),
          );
          return [header.join(' | '), ...rows].filter(Boolean).join('\n');
        }

        default:
          if (token.tokens?.length) {
            return flattenBlockTokens(token.tokens);
          }
          return token.text ?? '';
      }
    })
    .filter(Boolean)
    .join('\n');
}

function renderInlineTokens(tokens: MarkdownToken[], palette: MarkdownPalette): React.ReactNode[] {
  return tokens.flatMap((token, index) => {
    const key = `${token.type}-${index}`;

    switch (token.type) {
      case 'text':
      case 'escape':
        return [
          <Text key={key} color={palette.text}>
            {token.text ?? ''}
          </Text>,
        ];

      case 'strong':
        return [
          <Text key={key} color={palette.text} bold>
            {renderInlineTokens(token.tokens ?? [], palette)}
          </Text>,
        ];

      case 'em':
        return [
          <Text key={key} color={palette.accent} italic>
            {renderInlineTokens(token.tokens ?? [], palette)}
          </Text>,
        ];

      case 'codespan':
        return [
          <Text key={key} color={palette.code}>
            {`\`${token.text ?? ''}\``}
          </Text>,
        ];

      case 'del':
        return [
          <Text key={key} color={palette.muted}>
            {renderInlineTokens(token.tokens ?? [], palette)}
          </Text>,
        ];

      case 'link': {
        const labelTokens = token.tokens?.length ? token.tokens : textToken(token.text ?? '');
        return [
          <Text key={`${key}-label`} color={palette.link} underline>
            {renderInlineTokens(labelTokens, palette)}
          </Text>,
          <Text key={`${key}-href`} color={palette.muted}>
            {token.href ? ` (${token.href})` : ''}
          </Text>,
        ];
      }

      case 'br':
        return [<Text key={key}>{'\n'}</Text>];

      default:
        if (token.tokens?.length) {
          return [
            <Text key={key} color={palette.text}>
              {renderInlineTokens(token.tokens, palette)}
            </Text>,
          ];
        }
        return [
          <Text key={key} color={palette.text}>
            {token.text ?? ''}
          </Text>,
        ];
    }
  });
}

function renderListItem(
  item: NonNullable<MarkdownToken['items']>[number],
  index: number,
  ordered: boolean,
  palette: MarkdownPalette,
): React.ReactNode {
  const bullet = item.task ? (item.checked ? '[x]' : '[ ]') : ordered ? `${index + 1}.` : '•';
  const tokens = item.tokens?.length ? item.tokens : textToken(item.text ?? '');

  return (
    <Box key={`item-${index}`}>
      <Box width={4}>
        <Text color={palette.accent}>{bullet}</Text>
      </Box>
      <Box flexDirection="column" flexGrow={1}>
        {renderBlockTokens(tokens, `item-${index}`, palette)}
      </Box>
    </Box>
  );
}

function renderBlockTokens(
  tokens: MarkdownToken[],
  keyPrefix: string,
  palette: MarkdownPalette,
): React.ReactNode[] {
  return tokens.flatMap((token, index) => {
    const key = `${keyPrefix}-${index}-${token.type}`;

    switch (token.type) {
      case 'space':
        return [<Text key={key}>{' '}</Text>];

      case 'paragraph':
      case 'text': {
        const inlineTokens = token.tokens?.length ? token.tokens : textToken(token.text ?? '');
        return [
          <Box key={key} marginBottom={1}>
            <Text color={palette.text}>{renderInlineTokens(inlineTokens, palette)}</Text>
          </Box>,
        ];
      }

      case 'heading': {
        const inlineTokens = token.tokens?.length ? token.tokens : textToken(token.text ?? '');
        return [
          <Box key={key} marginBottom={1}>
            <Text color={palette.accent} bold>
              {`${'#'.repeat(token.depth ?? 1)} `}
              {renderInlineTokens(inlineTokens, palette)}
            </Text>
          </Box>,
        ];
      }

      case 'list':
        return [
          <Box key={key} flexDirection="column" marginBottom={1}>
            {(token.items ?? []).map((item, itemIndex) => renderListItem(item, itemIndex, Boolean(token.ordered), palette))}
          </Box>,
        ];

      case 'code':
        return [
          <Box key={key} borderStyle="round" borderColor="gray" paddingX={1} flexDirection="column" marginBottom={1}>
            {token.lang ? (
              <Text color={palette.muted}>{token.lang}</Text>
            ) : null}
            <Text color={palette.code}>{token.text ?? ''}</Text>
          </Box>,
        ];

      case 'blockquote':
        return [
          <Box key={key} marginLeft={1} marginBottom={1}>
            <Text color={palette.quote}>{`> ${flattenBlockTokens(token.tokens ?? [])}`}</Text>
          </Box>,
        ];

      case 'hr':
        return [
          <Text key={key} color={palette.muted}>
            {RULE}
          </Text>,
        ];

      case 'table': {
        const header = (token.header ?? []).map((cell) =>
          flattenInlineTokens(cell.tokens?.length ? cell.tokens : textToken(cell.text ?? '')),
        );
        const rows = (token.rows ?? []).map((row) =>
          row.map((cell) => flattenInlineTokens(cell.tokens?.length ? cell.tokens : textToken(cell.text ?? ''))),
        );
        const lines = [
          header.join(' | '),
          header.map(() => '---').join(' | '),
          ...rows.map((row) => row.join(' | ')),
        ].filter(Boolean);

        return [
          <Box key={key} borderStyle="round" borderColor="gray" paddingX={1} flexDirection="column">
            {lines.map((line, lineIndex) => (
              <Text key={`${key}-line-${lineIndex}`} color={lineIndex === 0 ? palette.accent : palette.text}>
                {line}
              </Text>
            ))}
          </Box>,
        ];
      }

      default:
        if (token.tokens?.length) {
          return renderBlockTokens(token.tokens, key, palette);
        }
        return token.text
          ? [
              <Text key={key} color={palette.text}>
                {token.text}
              </Text>,
            ]
          : [];
    }
  });
}

export function MarkdownMessage({content, palette}: MarkdownMessageProps) {
  const resolvedPalette: MarkdownPalette = {
    ...DEFAULT_PALETTE,
    ...palette,
  };
  const tokens = marked.lexer(content, {gfm: true, breaks: true}) as MarkdownToken[];

  return <Box flexDirection="column">{renderBlockTokens(tokens, 'md', resolvedPalette)}</Box>;
}