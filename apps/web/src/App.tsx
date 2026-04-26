import {useCallback, useEffect, useMemo, useRef, useState, type CSSProperties, type PointerEvent as ReactPointerEvent, type ReactNode} from 'react';
import {
  activateMessageVersion,
  deleteConversation,
  deleteMessage,
  downloadConversation,
  fetchConversations,
  fetchMessages,
  fetchModels,
  fetchProviders,
  fetchStatus,
  streamChat,
  streamEditMessage,
  streamRegenerate,
  updateConversationTitle,
} from './api';
import {MarkdownMessage} from './components/MarkdownMessage';
import {WorkspacePanel} from './components/WorkspacePanel';
import type {
  AppStatus,
  ConversationSummary,
  MessageRecord,
  ModelInfo,
  ProviderInfo,
  TranscriptBlock,
  UIStreamEvent,
} from './types';

const DELTA_FLUSH_MS = 48;
const SIDEBAR_MIN = 220;
const SIDEBAR_MAX = 480;
const INSPECTOR_MIN = 280;
const INSPECTOR_MAX = 620;

type AssistantTranscriptGroup = {
  id: string;
  kind: 'assistant-group';
  blocks: TranscriptBlock[];
};

type AssistantTimelineEntry = {
  id: string;
  stepNumber: number;
  kind: 'reasoning' | 'tool' | 'assistant';
  title: string;
  subtitle: string;
  status?: TranscriptBlock['status'];
  elapsed?: number;
  collapsible: boolean;
  collapsed: boolean;
  badges: string[];
};

type TranscriptViewItem = TranscriptBlock | AssistantTranscriptGroup;

type EditingState = {
  messageId: string;
  originalText: string;
};

type ExportFormat = 'markdown' | 'json' | 'txt';

type DensityMode = 'comfortable' | 'compact' | 'spacious';

type AppearanceSettings = {
  uiFont: string;
  codeFont: string;
  contentFontSize: number;
  codeFontSize: number;
  readingWidth: number;
  lineHeight: number;
  density: DensityMode;
};

const APPEARANCE_STORAGE_KEY = 'local-ai-agent.appearance.v1';

const DEFAULT_UI_FONT = 'Inter, "Segoe UI", "Microsoft YaHei UI", "PingFang SC", "Noto Sans CJK SC", ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, sans-serif';
const DEFAULT_CODE_FONT = '"Cascadia Code", "JetBrains Mono", "Sarasa Mono SC", "SFMono-Regular", Consolas, "Liberation Mono", monospace';

const DEFAULT_APPEARANCE: AppearanceSettings = {
  uiFont: DEFAULT_UI_FONT,
  codeFont: DEFAULT_CODE_FONT,
  contentFontSize: 15,
  codeFontSize: 13,
  readingWidth: 880,
  lineHeight: 1.75,
  density: 'comfortable',
};

const UI_FONT_PRESETS = [
  {label: '系统默认', value: DEFAULT_UI_FONT},
  {label: 'Segoe UI / 微软雅黑', value: '"Segoe UI", "Microsoft YaHei UI", "Microsoft YaHei", system-ui, sans-serif'},
  {label: 'PingFang / Noto Sans', value: '"PingFang SC", "Noto Sans CJK SC", "Microsoft YaHei UI", system-ui, sans-serif'},
  {label: 'Inter', value: 'Inter, "Segoe UI", "Microsoft YaHei UI", system-ui, sans-serif'},
];

const CODE_FONT_PRESETS = [
  {label: '系统默认', value: DEFAULT_CODE_FONT},
  {label: 'Cascadia Code', value: '"Cascadia Code", "Sarasa Mono SC", Consolas, monospace'},
  {label: 'JetBrains Mono', value: '"JetBrains Mono", "Sarasa Mono SC", Consolas, monospace'},
  {label: 'Consolas', value: 'Consolas, "Courier New", monospace'},
];

const DENSITY_OPTIONS: Array<{label: string; value: DensityMode; scale: number}> = [
  {label: '紧凑', value: 'compact', scale: 0.86},
  {label: '标准', value: 'comfortable', scale: 1},
  {label: '宽松', value: 'spacious', scale: 1.14},
];

type ConversationSearchResult = {
  conversation_id: string;
  title: string;
  model?: string;
  updated_at?: string;
  matched_message_id?: string | null;
  matched_role?: string | null;
  snippet: string;
};

type ConversationSearchPayload = {
  query?: string;
  count?: number;
  results?: ConversationSearchResult[];
};

type ConversationReadMessage = {
  id: string;
  role: string;
  created_at?: string;
  content?: string;
};

type ConversationReadPayload = {
  conversation?: {
    id: string;
    title: string;
    model?: string;
    updated_at?: string;
  };
  message_count?: number;
  messages?: ConversationReadMessage[];
};

function clamp(value: number, min: number, max: number): number {
  return Math.min(Math.max(value, min), max);
}

function normalizeAppearanceSettings(value: Partial<AppearanceSettings> | null | undefined): AppearanceSettings {
  return {
    uiFont: typeof value?.uiFont === 'string' && value.uiFont.trim() ? value.uiFont.trim() : DEFAULT_APPEARANCE.uiFont,
    codeFont: typeof value?.codeFont === 'string' && value.codeFont.trim() ? value.codeFont.trim() : DEFAULT_APPEARANCE.codeFont,
    contentFontSize: clamp(Number(value?.contentFontSize) || DEFAULT_APPEARANCE.contentFontSize, 13, 19),
    codeFontSize: clamp(Number(value?.codeFontSize) || DEFAULT_APPEARANCE.codeFontSize, 12, 17),
    readingWidth: clamp(Number(value?.readingWidth) || DEFAULT_APPEARANCE.readingWidth, 420, 1120),
    lineHeight: clamp(Number(value?.lineHeight) || DEFAULT_APPEARANCE.lineHeight, 1.45, 2.05),
    density: value?.density === 'compact' || value?.density === 'spacious' ? value.density : DEFAULT_APPEARANCE.density,
  };
}

function loadAppearanceSettings(): AppearanceSettings {
  try {
    const raw = window.localStorage.getItem(APPEARANCE_STORAGE_KEY);
    return normalizeAppearanceSettings(raw ? JSON.parse(raw) as Partial<AppearanceSettings> : null);
  } catch {
    return DEFAULT_APPEARANCE;
  }
}

function densityScale(density: DensityMode): number {
  return DENSITY_OPTIONS.find((item) => item.value === density)?.scale ?? 1;
}

function formatTime(value?: string): string {
  if (!value) {
    return '';
  }
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return value;
  }
  return date.toLocaleString('zh-CN', {month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit'});
}

function eventText(event: UIStreamEvent, key: string): string {
  const value = event.data[key];
  return typeof value === 'string' ? value : '';
}

function formatToolElapsed(value?: number): string {
  if (typeof value !== 'number' || !Number.isFinite(value)) {
    return '';
  }
  return `${value.toFixed(value >= 10 ? 0 : 1)}s`;
}

function formatToolParamValue(value: unknown): string {
  if (typeof value === 'string') {
    return value;
  }
  if (typeof value === 'number' || typeof value === 'boolean') {
    return String(value);
  }
  if (value == null) {
    return 'null';
  }
  try {
    return JSON.stringify(value, null, 2);
  } catch {
    return String(value);
  }
}

function looksStructuredToolOutput(text: string): boolean {
  const trimmed = text.trim();
  return trimmed.startsWith('{') || trimmed.startsWith('[');
}

function escapeRegExp(value: string): string {
  return value.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
}

function splitStoredToolContent(text: string): {headline: string; detail: string} {
  const normalized = text.replace(/^\[(ok|error)\]\s*/i, '').trim();
  const newlineIndex = normalized.indexOf('\n');
  if (newlineIndex < 0) {
    return {headline: normalized, detail: normalized};
  }
  const headline = normalized.slice(0, newlineIndex).trim();
  const detail = normalized.slice(newlineIndex + 1).trim();
  return {headline, detail: detail || headline};
}

function parseToolJson<T>(text: string): T | null {
  const detail = splitStoredToolContent(text).detail;
  if (!looksStructuredToolOutput(detail)) {
    return null;
  }
  try {
    return JSON.parse(detail) as T;
  } catch {
    return null;
  }
}

function structuredToolPayload<T>(block: TranscriptBlock): T | null {
  if (block.toolResult !== undefined && block.toolResult !== null) {
    return block.toolResult as T;
  }
  return parseToolJson<T>(block.text);
}

function roleLabel(role?: string | null): string {
  if (role === 'user') {
    return '用户命中';
  }
  if (role === 'assistant') {
    return '回答命中';
  }
  if (role === 'tool') {
    return '工具命中';
  }
  return '内容命中';
}

function highlightText(text: string, query: string): ReactNode {
  const needle = query.trim();
  if (!needle) {
    return text;
  }
  const pattern = new RegExp(escapeRegExp(needle), 'ig');
  const parts: ReactNode[] = [];
  let lastIndex = 0;
  let match = pattern.exec(text);
  while (match) {
    if (match.index > lastIndex) {
      parts.push(text.slice(lastIndex, match.index));
    }
    parts.push(<mark key={`${match.index}-${match[0]}`}>{match[0]}</mark>);
    lastIndex = match.index + match[0].length;
    match = pattern.exec(text);
  }
  if (lastIndex < text.length) {
    parts.push(text.slice(lastIndex));
  }
  return parts.length ? parts : text;
}

function ToolDetailSection({title, children}: {title: string; children: ReactNode}) {
  return (
    <section className="tool-section">
      <div className="tool-section-title">{title}</div>
      <div className="tool-section-body">{children}</div>
    </section>
  );
}

function ToolDetail({
  block,
  loadingLabel,
  onOpenConversation,
}: {
  block: TranscriptBlock;
  loadingLabel: string;
  onOpenConversation?: (conversationId: string) => void | Promise<void>;
}) {
  const hasParams = Boolean(block.params && Object.keys(block.params).length);
  const hasOutput = Boolean(block.text);
  const searchPayload = block.label === 'conversation_search' ? structuredToolPayload<ConversationSearchPayload>(block) : null;
  const readPayload = block.label === 'conversation_read' ? structuredToolPayload<ConversationReadPayload>(block) : null;
  const structuredResultText = block.toolResult !== undefined && block.toolResult !== null
    ? formatToolParamValue(block.toolResult)
    : '';
  const searchQuery = typeof block.params?.query === 'string'
    ? block.params.query
    : typeof searchPayload?.query === 'string'
      ? searchPayload.query
      : '';

  const renderToolResult = () => {
    if (searchPayload?.results) {
      return (
        <div className="history-search-results">
          {searchPayload.results.map((result) => (
            <article key={result.conversation_id} className="history-hit-card">
              <div className="history-hit-card-header">
                <div>
                  <h4>{highlightText(result.title, searchQuery)}</h4>
                  <div className="history-hit-meta">
                    <span>{roleLabel(result.matched_role)}</span>
                    {result.updated_at ? <span>{formatTime(result.updated_at)}</span> : null}
                    {result.model ? <span>{result.model}</span> : null}
                  </div>
                </div>
                {onOpenConversation ? (
                  <button
                    type="button"
                    className="ghost-button tiny"
                    onClick={() => { void onOpenConversation(result.conversation_id); }}
                  >打开会话</button>
                ) : null}
              </div>
              <p className="history-hit-snippet">{highlightText(result.snippet, searchQuery)}</p>
            </article>
          ))}
        </div>
      );
    }

    if (readPayload?.conversation) {
      return (
        <div className="history-read-view">
          <article className="history-hit-card history-hit-card-primary">
            <div className="history-hit-card-header">
              <div>
                <h4>{readPayload.conversation.title}</h4>
                <div className="history-hit-meta">
                  <span>来源会话</span>
                  {readPayload.conversation.updated_at ? <span>{formatTime(readPayload.conversation.updated_at)}</span> : null}
                  {readPayload.conversation.model ? <span>{readPayload.conversation.model}</span> : null}
                  {typeof readPayload.message_count === 'number' ? <span>{readPayload.message_count} 条消息</span> : null}
                </div>
              </div>
              {onOpenConversation && readPayload.conversation.id ? (
                <button
                  type="button"
                  className="ghost-button tiny"
                  onClick={() => { void onOpenConversation(readPayload.conversation?.id ?? ''); }}
                >打开会话</button>
              ) : null}
            </div>
          </article>
          <div className="history-read-messages">
            {(readPayload.messages ?? []).map((message) => (
              <article key={message.id} className="history-read-message">
                <div className="history-read-message-header">
                  <span>{roleLabel(message.role).replace('命中', '')}</span>
                  {message.created_at ? <span>{formatTime(message.created_at)}</span> : null}
                </div>
                <p>{message.content?.trim() || '(空)'}</p>
              </article>
            ))}
          </div>
        </div>
      );
    }

    return hasOutput || structuredResultText ? (
      <pre className={block.toolResult != null || looksStructuredToolOutput(block.text) ? 'tool-output structured' : 'tool-output'}>{structuredResultText || block.text}</pre>
    ) : (
      <LoadingDots label={loadingLabel} />
    );
  };

  return (
    <div className="tool-detail">
      {block.summary ? (
        <ToolDetailSection title="摘要">
          <p className="tool-summary">{block.summary}</p>
        </ToolDetailSection>
      ) : null}
      {hasParams ? (
        <ToolDetailSection title="参数">
          <dl className="tool-params-list">
            {Object.entries(block.params ?? {}).map(([key, value]) => (
              <div key={key} className="tool-param-row">
                <dt>{key}</dt>
                <dd><pre>{formatToolParamValue(value)}</pre></dd>
              </div>
            ))}
          </dl>
        </ToolDetailSection>
      ) : null}
      <ToolDetailSection title={block.status === 'running' ? '执行中' : '结果'}>
        {renderToolResult()}
      </ToolDetailSection>
    </div>
  );
}

function readConversationSummary(event: UIStreamEvent): ConversationSummary | null {
  const value = event.data.conversation;
  if (!value || typeof value !== 'object') {
    return null;
  }
  const item = value as Partial<ConversationSummary>;
  return item.id && item.title && item.created_at && item.updated_at
    ? {
        id: item.id,
        title: item.title,
        model: item.model ?? '',
        created_at: item.created_at,
        updated_at: item.updated_at,
      }
    : null;
}

function upsertConversation(
  conversations: ConversationSummary[],
  next: ConversationSummary,
): ConversationSummary[] {
  const exists = conversations.some((item) => item.id === next.id);
  const merged = exists
    ? conversations.map((item) => (item.id === next.id ? next : item))
    : [next, ...conversations];
  return [...merged].sort((a, b) => b.updated_at.localeCompare(a.updated_at));
}

function appendOrUpdate(
  blocks: TranscriptBlock[],
  id: string,
  fallback: TranscriptBlock,
  updater: (block: TranscriptBlock) => TranscriptBlock,
): TranscriptBlock[] {
  let found = false;
  const updated = blocks.map((block) => {
    if (block.id !== id) {
      return block;
    }
    found = true;
    return updater(block);
  });
  return found ? updated : [...updated, updater(fallback)];
}

function insertBeforeBlock(
  blocks: TranscriptBlock[],
  beforeId: string | null,
  block: TranscriptBlock,
): TranscriptBlock[] {
  if (!beforeId) {
    return [...blocks, block];
  }
  const index = blocks.findIndex((item) => item.id === beforeId);
  if (index < 0) {
    return [...blocks, block];
  }
  return [...blocks.slice(0, index), block, ...blocks.slice(index)];
}

function isAssistantSideBlock(block: TranscriptBlock): boolean {
  return block.kind === 'assistant' || block.kind === 'reasoning' || block.kind === 'tool';
}

function buildTranscriptViewItems(blocks: TranscriptBlock[]): TranscriptViewItem[] {
  const items: TranscriptViewItem[] = [];
  let group: AssistantTranscriptGroup | null = null;

  for (const block of blocks) {
    if (isAssistantSideBlock(block)) {
      if (!group) {
        group = {id: `assistant-group-${block.id}`, kind: 'assistant-group', blocks: []};
        items.push(group);
      }
      group.blocks.push(block);
      continue;
    }

    group = null;
    items.push(block);
  }

  return items;
}

function assistantGroupStatus(blocks: TranscriptBlock[]): TranscriptBlock['status'] | undefined {
  if (blocks.some((block) => block.status === 'running' || block.placeholder)) {
    return 'running';
  }
  if (blocks.some((block) => block.status === 'error')) {
    return 'error';
  }
  return undefined;
}

function orderedAssistantBlocks(blocks: TranscriptBlock[]): TranscriptBlock[] {
  return [
    ...blocks.filter((block) => block.kind !== 'assistant'),
    ...blocks.filter((block) => block.kind === 'assistant'),
  ];
}

function buildAssistantTimeline(
  blocks: TranscriptBlock[],
  groupStatus: TranscriptBlock['status'] | undefined,
  versionNumber: number,
  versionCount: number,
): AssistantTimelineEntry[] {
  const toolAttempts = new Map<string, number>();

  return blocks.map((block, index) => {
    const stepNumber = index + 1;

    if (block.kind === 'assistant') {
      const badges = versionCount > 1 ? [`版本 ${versionNumber}/${versionCount}`] : [];
      if ((block.status ?? groupStatus) === 'error') {
        badges.push('失败');
      }
      return {
        id: block.id,
        stepNumber,
        kind: 'assistant',
        title: block.text ? '生成回答' : '等待回答',
        subtitle: block.text ? `${block.text.length} 字输出` : '模型仍在生成本版本回答',
        status: block.status ?? groupStatus ?? 'ok',
        collapsible: false,
        collapsed: false,
        badges,
      } satisfies AssistantTimelineEntry;
    }

    if (block.kind === 'reasoning') {
      const badges: string[] = [];
      if ((block.status ?? groupStatus) === 'error') {
        badges.push('失败');
      }
      return {
        id: block.id,
        stepNumber,
        kind: 'reasoning',
        title: '思考过程',
        subtitle: block.text ? `${block.text.length} 字推理` : '模型正在分析问题',
        status: block.status ?? groupStatus,
        collapsible: Boolean(block.collapsible),
        collapsed: Boolean(block.collapsed),
        badges,
      } satisfies AssistantTimelineEntry;
    }

    const toolKey = block.label || 'tool';
    const attempt = (toolAttempts.get(toolKey) ?? 0) + 1;
    toolAttempts.set(toolKey, attempt);
    const badges: string[] = [];
    if (attempt > 1) {
      badges.push(`重试 ${attempt - 1}`);
    }
    if ((block.status ?? groupStatus) === 'error') {
      badges.push('失败节点');
    }
    return {
      id: block.id,
      stepNumber,
      kind: 'tool',
      title: `调用 ${block.label}`,
      subtitle: block.summary || (block.toolResult != null ? '结构化结果已保存' : block.text ? '已返回结果' : '等待结果'),
      status: block.status ?? groupStatus,
      elapsed: block.elapsed,
      collapsible: Boolean(block.collapsible),
      collapsed: Boolean(block.collapsed),
      badges,
    } satisfies AssistantTimelineEntry;
  });
}

async function copyText(text: string): Promise<boolean> {
  if (!text) {
    return false;
  }
  try {
    if (navigator.clipboard?.writeText) {
      await navigator.clipboard.writeText(text);
      return true;
    }
  } catch {
    // fall through to legacy path
  }
  try {
    const textarea = document.createElement('textarea');
    textarea.value = text;
    textarea.setAttribute('readonly', '');
    textarea.style.position = 'fixed';
    textarea.style.opacity = '0';
    document.body.appendChild(textarea);
    textarea.select();
    const ok = document.execCommand('copy');
    document.body.removeChild(textarea);
    return ok;
  } catch {
    return false;
  }
}

function LoadingDots({label}: {label: string}) {
  return (
    <div className="stream-placeholder">
      <span className="typing-dots" aria-hidden="true"><i /> <i /> <i /></span>
      <span>{label}</span>
    </div>
  );
}

function buildBlocksFromMessages(messages: MessageRecord[]): TranscriptBlock[] {
  return messages.flatMap((message) => {
    if (message.role === 'assistant') {
      const blocks: TranscriptBlock[] = [];
      if (message.thinking) {
        blocks.push({
          id: `${message.id}-thinking`,
          kind: 'reasoning',
          label: '思考过程',
          text: message.thinking,
          status: 'ok',
          collapsible: true,
          collapsed: true,
          createdAt: message.created_at,
        });
      }
      blocks.push({
        id: message.id,
        kind: 'assistant',
        label: 'assistant',
        text: message.content,
        messageId: message.id,
        createdAt: message.created_at,
        responseToMessageId: message.response_to_message_id,
        versionNumber: message.version_number,
        versionCount: message.version_count,
      });
      return blocks;
    }

    if (message.role === 'tool') {
      const {headline, detail} = splitStoredToolContent(message.content);
      return [{
        id: message.id,
        kind: 'tool' as const,
        label: message.tool_name || 'tool',
        text: detail,
        summary: headline,
        messageId: message.id,
        toolResult: message.tool_result,
        status: message.content.startsWith('[ok]') ? 'ok' : message.content.startsWith('[error]') ? 'error' : undefined,
        collapsible: true,
        collapsed: true,
        createdAt: message.created_at,
      }];
    }

    return [{
      id: message.id,
      kind: message.role === 'user' ? 'user' as const : 'meta' as const,
      label: message.role === 'user' ? '你' : message.role,
      text: message.content,
      messageId: message.id,
      createdAt: message.created_at,
    }];
  });
}

function EmptyState() {
  return (
    <div className="empty-state">
      <div className="empty-logo">✦</div>
      <h1>Local AI Agent</h1>
      <p>网页聊天界面已启用。选择会话或直接输入问题开始。</p>
      <div className="empty-grid">
        <span>流式输出</span>
        <span>Markdown</span>
        <span>工具调用</span>
        <span>文件工作区</span>
      </div>
    </div>
  );
}

function AssistantSection({block, onToggle, onOpenConversation}: {block: TranscriptBlock; onToggle: (id: string) => void; onOpenConversation?: (conversationId: string) => void | Promise<void>}) {
  if (block.kind === 'assistant') {
    return (
      <div className="assistant-answer-section">
        {block.text ? <MarkdownMessage content={block.text} /> : <LoadingDots label="正在组织回答..." />}
      </div>
    );
  }

  const title = block.kind === 'reasoning' ? '思考过程' : `工具调用 · ${block.label}`;
  const collapsed = Boolean(block.collapsible && block.collapsed);
  const content = collapsed ? null : block.kind === 'tool' ? (
    <ToolDetail block={block} loadingLabel="正在调用工具..." onOpenConversation={onOpenConversation} />
  ) : block.text ? (
    <pre className="plain-pre">{block.text}</pre>
  ) : (
    <LoadingDots label="正在思考..." />
  );

  return (
    <section className={`assistant-subblock assistant-subblock-${block.kind}${collapsed ? ' is-collapsed' : ''}`}>
      <header className="assistant-subblock-header">
        <span>{title}</span>
        {block.status ? <span className={`status-pill status-${block.status}`}>{block.status}</span> : null}
        {block.elapsed ? <span className="tool-elapsed">{formatToolElapsed(block.elapsed)}</span> : null}
        {block.collapsible ? (
          <button type="button" className="ghost-button tiny" aria-expanded={!collapsed} onClick={() => onToggle(block.id)}>
            {collapsed ? '展开' : '折叠'}
          </button>
        ) : null}
      </header>
      {content ? <div className="assistant-subblock-content">{content}</div> : null}
    </section>
  );
}

function AssistantTranscriptItem({
  group,
  onToggle,
  onCopy,
  onRegenerate,
  onSwitchVersion,
  onDeleteMessage,
  onOpenConversation,
  busy,
}: {
  group: AssistantTranscriptGroup;
  onToggle: (id: string) => void;
  onCopy: (text: string) => void;
  onRegenerate: () => void;
  onSwitchVersion: (messageId: string, versionNumber: number) => void;
  onDeleteMessage: (messageId: string) => void;
  onOpenConversation: (conversationId: string) => void | Promise<void>;
  busy: boolean;
}) {
  const status = assistantGroupStatus(group.blocks);
  const createdAt = group.blocks.find((block) => block.createdAt)?.createdAt;
  const ordered = orderedAssistantBlocks(group.blocks);
  const hasAnswer = ordered.some((block) => block.kind === 'assistant');
  const answerBlock = ordered.find((block) => block.kind === 'assistant');
  const answerText = answerBlock?.text ?? '';
  const answerMessageId = answerBlock?.messageId ?? null;
  const versionNumber = answerBlock?.versionNumber ?? 1;
  const versionCount = answerBlock?.versionCount ?? 1;
  const canSwitchVersion = Boolean(answerMessageId) && versionCount > 1;
  const isRunning = status === 'running';
  const timeline = buildAssistantTimeline(ordered, status, versionNumber, versionCount);
  const showTimeline = timeline.length > 1;

  return (
    <article className="message message-assistant message-assistant-group">
      <div className="avatar">AI</div>
      <div className="message-body">
        <header className="message-header">
          <span>assistant</span>
          {status ? <span className={`status-pill status-${status}`}>{status === 'running' ? '生成中' : status}</span> : null}
          {createdAt ? <time>{formatTime(createdAt)}</time> : null}
        </header>
        {showTimeline ? (
          <section className="assistant-timeline-card" aria-label="工具执行时间线">
            <div className="assistant-timeline-header">
              <div className="assistant-timeline-title">工具时间线聚合</div>
              {canSwitchVersion ? <div className="assistant-timeline-version">联动版本 {versionNumber} / {versionCount}</div> : null}
            </div>
            <ol className="assistant-timeline-list">
              {timeline.map((entry) => (
                <li key={entry.id} className={`assistant-timeline-item assistant-timeline-item-${entry.kind}${entry.collapsible && entry.collapsed ? ' is-collapsed' : ''}${entry.status === 'error' ? ' is-error' : ''}${entry.badges.some((badge) => badge.startsWith('重试')) ? ' is-retry' : ''}`}>
                  {entry.collapsible ? (
                    <button type="button" className="assistant-timeline-button" aria-expanded={!entry.collapsed} onClick={() => onToggle(entry.id)}>
                      <span className="assistant-timeline-main">
                        <span className="assistant-timeline-heading">
                          <span className="assistant-timeline-step">Step {entry.stepNumber}</span>
                          <strong>{entry.title}</strong>
                          {entry.badges.map((badge) => <span key={badge} className="assistant-timeline-badge">{badge}</span>)}
                        </span>
                        <span>{entry.subtitle}</span>
                      </span>
                      <span className="assistant-timeline-meta">
                        {entry.elapsed ? <span>{formatToolElapsed(entry.elapsed)}</span> : null}
                        {entry.status ? <span className={`status-pill status-${entry.status}`}>{entry.status}</span> : null}
                        <span>{entry.collapsed ? '展开' : '查看'}</span>
                      </span>
                    </button>
                  ) : (
                    <div className="assistant-timeline-button assistant-timeline-static">
                      <span className="assistant-timeline-main">
                        <span className="assistant-timeline-heading">
                          <span className="assistant-timeline-step">Step {entry.stepNumber}</span>
                          <strong>{entry.title}</strong>
                          {entry.badges.map((badge) => <span key={badge} className="assistant-timeline-badge">{badge}</span>)}
                        </span>
                        <span>{entry.subtitle}</span>
                      </span>
                      <span className="assistant-timeline-meta">
                        {entry.elapsed ? <span>{formatToolElapsed(entry.elapsed)}</span> : null}
                        {entry.status ? <span className={`status-pill status-${entry.status}`}>{entry.status}</span> : null}
                      </span>
                    </div>
                  )}
                </li>
              ))}
            </ol>
          </section>
        ) : null}
        <div className="assistant-sections">
          {ordered.map((block) => <AssistantSection key={block.id} block={block} onToggle={onToggle} onOpenConversation={onOpenConversation} />)}
          {!hasAnswer && status === 'running' ? <LoadingDots label="正在等待模型输出..." /> : null}
        </div>
        <footer className="message-actions">
          {canSwitchVersion && answerMessageId ? (
            <div className="message-version-switch" aria-label="回答版本切换">
              <button
                type="button"
                className="ghost-button tiny"
                disabled={busy || isRunning || versionNumber <= 1}
                onClick={() => onSwitchVersion(answerMessageId, versionNumber - 1)}
                title="切换到上一版"
              >上一版</button>
              <span key={versionNumber}>版本 {versionNumber} / {versionCount}</span>
              <button
                type="button"
                className="ghost-button tiny"
                disabled={busy || isRunning || versionNumber >= versionCount}
                onClick={() => onSwitchVersion(answerMessageId, versionNumber + 1)}
                title="切换到下一版"
              >下一版</button>
            </div>
          ) : null}
          <button
            type="button"
            className="ghost-button tiny"
            disabled={!answerText}
            onClick={() => onCopy(answerText)}
            title="复制回答"
          >复制</button>
          <button
            type="button"
            className="ghost-button tiny"
            disabled={busy}
            onClick={onRegenerate}
            title="重新生成"
          >重新生成</button>
          {answerMessageId ? (
            <button
              type="button"
              className="ghost-button tiny"
              disabled={busy || isRunning}
              onClick={() => onDeleteMessage(answerMessageId)}
              title="删除此回答"
            >删除</button>
          ) : null}
        </footer>
      </div>
    </article>
  );
}

function TranscriptItem({
  block,
  onToggle,
  onCopy,
  onEditMessage,
  onDeleteMessage,
  onOpenConversation,
  busy,
}: {
  block: TranscriptBlock;
  onToggle: (id: string) => void;
  onCopy: (text: string) => void;
  onEditMessage: (block: TranscriptBlock) => void;
  onDeleteMessage: (messageId: string) => void;
  onOpenConversation: (conversationId: string) => void | Promise<void>;
  busy: boolean;
}) {
  const icon = {
    user: '你',
    assistant: 'AI',
    reasoning: '思',
    tool: 'Tool',
    meta: 'i',
    error: '!',
  }[block.kind];

  const content = block.collapsible && block.collapsed ? null : block.kind === 'assistant' ? (
    <MarkdownMessage content={block.text || ' '} />
  ) : block.kind === 'user' ? (
    <p className="plain-text">{block.text}</p>
  ) : block.kind === 'tool' ? (
    <ToolDetail block={block} loadingLabel="正在调用工具..." onOpenConversation={onOpenConversation} />
  ) : (
    <pre className="plain-pre">{block.text}</pre>
  );

  const showActions = block.kind === 'user' && Boolean(block.messageId);

  return (
    <article className={`message message-${block.kind}`}>
      <div className="avatar">{icon}</div>
      <div className="message-body">
        <header className="message-header">
          <span>{block.label}</span>
          {block.status ? <span className={`status-pill status-${block.status}`}>{block.status}</span> : null}
          {block.createdAt ? <time>{formatTime(block.createdAt)}</time> : null}
          {block.collapsible ? (
            <button type="button" className="ghost-button tiny" aria-expanded={!block.collapsed} onClick={() => onToggle(block.id)}>
              {block.collapsed ? '展开' : '折叠'}
            </button>
          ) : null}
        </header>
        {content}
        {showActions ? (
          <footer className="message-actions">
            <button
              type="button"
              className="ghost-button tiny"
              disabled={!block.text}
              onClick={() => onCopy(block.text)}
              title="复制内容"
            >复制</button>
            <button
              type="button"
              className="ghost-button tiny"
              disabled={busy}
              onClick={() => onEditMessage(block)}
              title="编辑并重新发送"
            >编辑</button>
            <button
              type="button"
              className="ghost-button tiny"
              disabled={busy}
              onClick={() => block.messageId && onDeleteMessage(block.messageId)}
              title="删除此消息"
            >删除</button>
          </footer>
        ) : null}
      </div>
    </article>
  );
}

function ConversationItem({
  item,
  active,
  onOpen,
  onRename,
  onDelete,
  onExport,
}: {
  item: ConversationSummary;
  active: boolean;
  onOpen: () => void;
  onRename: () => void;
  onDelete: () => void;
  onExport: () => void;
}) {
  const handleKeyDown = (event: React.KeyboardEvent<HTMLDivElement>) => {
    if (event.target !== event.currentTarget) return;
    if (event.key === 'Enter' || event.key === ' ') {
      event.preventDefault();
      onOpen();
    }
  };

  return (
    <div
      role="button"
      tabIndex={0}
      className={`conversation-item${active ? ' active' : ''}`}
      onClick={onOpen}
      onKeyDown={handleKeyDown}
    >
      <span className="conversation-title">{item.title}</span>
      <span className="conversation-meta">{item.model || 'default'} · {formatTime(item.updated_at)}</span>
      <span className="conversation-actions" onClick={(event) => event.stopPropagation()}>
        <button type="button" title="导出 Markdown" onClick={onExport}>↓</button>
        <button type="button" title="重命名" onClick={onRename}>✎</button>
        <button type="button" title="删除" onClick={onDelete}>×</button>
      </span>
    </div>
  );
}

function AppearanceSettingsPanel({
  value,
  onChange,
  onReset,
}: {
  value: AppearanceSettings;
  onChange: (next: AppearanceSettings) => void;
  onReset: () => void;
}) {
  const update = (patch: Partial<AppearanceSettings>) => {
    onChange(normalizeAppearanceSettings({...value, ...patch}));
  };

  return (
    <section className="appearance-panel">
      <div className="appearance-panel-header">
        <h2>外观</h2>
        <button type="button" className="ghost-button tiny" onClick={onReset}>重置</button>
      </div>

      <label className="appearance-field">
        <span>正文字体</span>
        <select value={value.uiFont} onChange={(event) => update({uiFont: event.target.value})}>
          {UI_FONT_PRESETS.map((preset) => <option key={preset.label} value={preset.value}>{preset.label}</option>)}
          {!UI_FONT_PRESETS.some((preset) => preset.value === value.uiFont) ? <option value={value.uiFont}>自定义</option> : null}
        </select>
      </label>
      <label className="appearance-field">
        <span>自定义正文字体</span>
        <input
          value={value.uiFont}
          onChange={(event) => update({uiFont: event.target.value})}
          placeholder={'"Segoe UI", "Microsoft YaHei UI", sans-serif'}
        />
      </label>

      <label className="appearance-field">
        <span>代码字体</span>
        <select value={value.codeFont} onChange={(event) => update({codeFont: event.target.value})}>
          {CODE_FONT_PRESETS.map((preset) => <option key={preset.label} value={preset.value}>{preset.label}</option>)}
          {!CODE_FONT_PRESETS.some((preset) => preset.value === value.codeFont) ? <option value={value.codeFont}>自定义</option> : null}
        </select>
      </label>
      <label className="appearance-field">
        <span>自定义代码字体</span>
        <input
          value={value.codeFont}
          onChange={(event) => update({codeFont: event.target.value})}
          placeholder={'"Cascadia Code", Consolas, monospace'}
        />
      </label>

      <div className="appearance-grid">
        <label className="appearance-field appearance-field-range">
          <span>正文字号 <em>{value.contentFontSize}px</em></span>
          <input
            type="range"
            min="13"
            max="19"
            step="1"
            value={value.contentFontSize}
            onChange={(event) => update({contentFontSize: Number(event.target.value)})}
          />
        </label>
        <label className="appearance-field appearance-field-range">
          <span>代码字号 <em>{value.codeFontSize}px</em></span>
          <input
            type="range"
            min="12"
            max="17"
            step="1"
            value={value.codeFontSize}
            onChange={(event) => update({codeFontSize: Number(event.target.value)})}
          />
        </label>
      </div>

      <label className="appearance-field appearance-field-range">
        <span>阅读宽度 <em>{value.readingWidth}px</em></span>
        <input
          type="range"
          min="420"
          max="1120"
          step="40"
          value={value.readingWidth}
          onChange={(event) => update({readingWidth: Number(event.target.value)})}
        />
      </label>

      <label className="appearance-field appearance-field-range">
        <span>上下行间距 <em>{value.lineHeight.toFixed(2)}</em></span>
        <input
          type="range"
          min="1.45"
          max="2.05"
          step="0.05"
          value={value.lineHeight}
          onChange={(event) => update({lineHeight: Number(event.target.value)})}
        />
      </label>

      <div className="appearance-density" role="group" aria-label="界面密度">
        {DENSITY_OPTIONS.map((option) => (
          <button
            type="button"
            key={option.value}
            className={value.density === option.value ? 'active' : ''}
            onClick={() => update({density: option.value})}
          >
            {option.label}
          </button>
        ))}
      </div>
    </section>
  );
}

function ModelPicker({
  models,
  value,
  onChange,
}: {
  models: ModelInfo[];
  value: string;
  onChange: (modelId: string) => void;
}) {
  const [open, setOpen] = useState(false);
  const pickerRef = useRef<HTMLDivElement | null>(null);
  const selectedModel = models.find((model) => model.id === value) ?? models[0];

  useEffect(() => {
    if (!open) {
      return;
    }
    const handlePointerDown = (event: PointerEvent) => {
      const target = event.target as Node | null;
      if (pickerRef.current && target && !pickerRef.current.contains(target)) {
        setOpen(false);
      }
    };
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        setOpen(false);
      }
    };
    window.addEventListener('pointerdown', handlePointerDown);
    window.addEventListener('keydown', handleKeyDown);
    return () => {
      window.removeEventListener('pointerdown', handlePointerDown);
      window.removeEventListener('keydown', handleKeyDown);
    };
  }, [open]);

  return (
    <div className="model-picker" ref={pickerRef}>
      <button
        type="button"
        className="model-picker-trigger"
        aria-haspopup="listbox"
        aria-expanded={open}
        disabled={!models.length}
        onClick={() => setOpen((current) => !current)}
      >
        <span>{selectedModel ? `${selectedModel.provider_name} / ${selectedModel.name}` : '未选择模型'}</span>
        <span aria-hidden="true" className="model-picker-chevron">⌄</span>
      </button>
      {open ? (
        <div className="model-picker-menu" role="listbox" aria-label="选择模型">
          {models.map((model) => (
            <button
              type="button"
              key={model.id}
              role="option"
              aria-selected={model.id === selectedModel?.id}
              className={model.id === selectedModel?.id ? 'active' : ''}
              onClick={() => {
                onChange(model.id);
                setOpen(false);
              }}
            >
              <span>{model.provider_name} / {model.name}</span>
              {model.id === selectedModel?.id ? <em>当前</em> : null}
            </button>
          ))}
        </div>
      ) : null}
    </div>
  );
}

export default function App() {
  const [status, setStatus] = useState<AppStatus | null>(null);
  const [providers, setProviders] = useState<ProviderInfo[]>([]);
  const [models, setModels] = useState<ModelInfo[]>([]);
  const [selectedModelId, setSelectedModelId] = useState<string>('');
  const [exportMenuOpen, setExportMenuOpen] = useState(false);
  const [conversations, setConversations] = useState<ConversationSummary[]>([]);
  const [conversationId, setConversationId] = useState<string | null>(null);
  const [blocks, setBlocks] = useState<TranscriptBlock[]>([]);
  const [input, setInput] = useState('');
  const [attachedPaths, setAttachedPaths] = useState<string[]>([]);
  const [editingMessage, setEditingMessage] = useState<EditingState | null>(null);
  const [conversationFilter, setConversationFilter] = useState('');
  const [toast, setToast] = useState<string>('');
  const [sidebarWidth, setSidebarWidth] = useState(300);
  const [inspectorWidth, setInspectorWidth] = useState(360);
  const [sidebarCollapsed, setSidebarCollapsed] = useState(false);
  const [inspectorCollapsed, setInspectorCollapsed] = useState(false);
  const [appearance, setAppearance] = useState<AppearanceSettings>(() => loadAppearanceSettings());
  const [busy, setBusy] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string>('');
  const deltaBufferRef = useRef<Record<string, {kind: 'assistant' | 'reasoning'; text: string}>>({});
  const deltaTimerRef = useRef<number | null>(null);
  const pendingPromptRef = useRef('');
  const pendingUserBlockIdRef = useRef<string | null>(null);
  const pendingAssistantBlockIdRef = useRef<string | null>(null);
  const abortRef = useRef<AbortController | null>(null);
  const transcriptRef = useRef<HTMLDivElement | null>(null);
  const exportMenuRef = useRef<HTMLDivElement | null>(null);
  const suppressNextAutoScrollRef = useRef(false);

  const selectedModel = useMemo(
    () => models.find((model) => model.id === selectedModelId) ?? models.find((model) => model.default) ?? models[0],
    [models, selectedModelId],
  );

  const transcriptItems = useMemo(() => buildTranscriptViewItems(blocks), [blocks]);
  const effectiveAppearance = useMemo(() => normalizeAppearanceSettings(appearance), [appearance]);

  const showToast = useCallback((message: string) => {
    setToast(message);
    window.setTimeout(() => setToast((current) => (current === message ? '' : current)), 2200);
  }, []);

  const handleCopy = useCallback(async (text: string) => {
    const ok = await copyText(text);
    showToast(ok ? '已复制到剪贴板' : '复制失败');
  }, [showToast]);

  const shellStyle = useMemo(() => ({
    '--sidebar-width': sidebarCollapsed ? '0px' : `${sidebarWidth}px`,
    '--inspector-width': inspectorCollapsed ? '0px' : `${inspectorWidth}px`,
    '--font-ui': effectiveAppearance.uiFont,
    '--font-code': effectiveAppearance.codeFont,
    '--content-font-size': `${effectiveAppearance.contentFontSize}px`,
    '--code-font-size': `${effectiveAppearance.codeFontSize}px`,
    '--reading-width': `${effectiveAppearance.readingWidth}px`,
    '--content-line-height': String(effectiveAppearance.lineHeight),
    '--density-scale': String(densityScale(effectiveAppearance.density)),
  }) as CSSProperties, [effectiveAppearance, inspectorCollapsed, inspectorWidth, sidebarCollapsed, sidebarWidth]);

  const updateAppearance = useCallback((next: AppearanceSettings) => {
    setAppearance(normalizeAppearanceSettings(next));
  }, []);

  const resetAppearance = useCallback(() => {
    setAppearance(DEFAULT_APPEARANCE);
    showToast('外观已重置');
  }, [showToast]);

  const startResize = useCallback((panel: 'sidebar' | 'inspector', event: ReactPointerEvent<HTMLButtonElement>) => {
    event.preventDefault();
    const handlePointerMove = (moveEvent: PointerEvent) => {
      if (panel === 'sidebar') {
        setSidebarWidth(clamp(moveEvent.clientX, SIDEBAR_MIN, SIDEBAR_MAX));
      } else {
        setInspectorWidth(clamp(window.innerWidth - moveEvent.clientX, INSPECTOR_MIN, INSPECTOR_MAX));
      }
    };
    const handlePointerUp = () => {
      window.removeEventListener('pointermove', handlePointerMove);
      window.removeEventListener('pointerup', handlePointerUp);
      document.body.classList.remove('is-resizing-layout');
    };
    document.body.classList.add('is-resizing-layout');
    window.addEventListener('pointermove', handlePointerMove);
    window.addEventListener('pointerup', handlePointerUp, {once: true});
  }, []);

  const flushDeltaBuffer = useCallback(() => {
    const buffered = Object.entries(deltaBufferRef.current);
    if (!buffered.length) {
      return;
    }
    deltaBufferRef.current = {};
    setBlocks((current) => {
      let next = current;
      for (const [id, item] of buffered) {
        next = appendOrUpdate(
          next,
          id,
          {
            id,
            kind: item.kind,
            label: item.kind === 'assistant' ? 'assistant' : '思考过程',
            text: '',
            status: 'running',
            collapsible: item.kind === 'reasoning',
            collapsed: false,
          },
          (block) => ({...block, text: `${block.text}${item.text}`, status: 'running', placeholder: false}),
        );
      }
      return next;
    });
  }, []);

  const queueDelta = useCallback((kind: 'assistant' | 'reasoning', blockId: string, text: string) => {
    const current = deltaBufferRef.current[blockId];
    deltaBufferRef.current[blockId] = current
      ? {...current, text: `${current.text}${text}`}
      : {kind, text};
    if (deltaTimerRef.current) {
      return;
    }
    deltaTimerRef.current = window.setTimeout(() => {
      deltaTimerRef.current = null;
      flushDeltaBuffer();
    }, DELTA_FLUSH_MS);
  }, [flushDeltaBuffer]);

  const refreshConversations = useCallback(async (preferredId?: string | null, query?: string) => {
    const next = await fetchConversations(query);
    setConversations(next);
    if (preferredId && next.some((item) => item.id === preferredId)) {
      setConversationId(preferredId);
    }
    return next;
  }, []);

  const openConversation = useCallback(async (targetId: string) => {
    flushDeltaBuffer();
    pendingUserBlockIdRef.current = null;
    pendingAssistantBlockIdRef.current = null;
    const messages = await fetchMessages(targetId);
    setConversationId(targetId);
    setBlocks(buildBlocksFromMessages(messages));
    setEditingMessage(null);
    setError('');
  }, [flushDeltaBuffer]);

  const resetConversation = () => {
    flushDeltaBuffer();
    pendingUserBlockIdRef.current = null;
    pendingAssistantBlockIdRef.current = null;
    setConversationId(null);
    setBlocks([]);
    setInput('');
    setAttachedPaths([]);
    setEditingMessage(null);
    setError('');
  };

  const toggleBlock = (id: string) => {
    suppressNextAutoScrollRef.current = true;
    setBlocks((current) => current.map((block) => (
      block.id === id ? {...block, collapsed: !block.collapsed} : block
    )));
  };

  const attachWorkspacePath = useCallback((path: string) => {
    setAttachedPaths((current) => current.includes(path) ? current : [...current, path]);
  }, []);

  const detachWorkspacePath = useCallback((path: string) => {
    setAttachedPaths((current) => current.filter((item) => item !== path));
  }, []);

  const beginEditMessage = useCallback((block: TranscriptBlock) => {
    if (block.kind !== 'user' || !block.messageId) {
      return;
    }
    setEditingMessage({messageId: block.messageId, originalText: block.text});
    setInput(block.text);
    setError('');
    showToast('已进入编辑模式');
  }, [showToast]);

  const cancelEditMessage = useCallback(() => {
    setEditingMessage(null);
    setInput('');
  }, []);

  const applyEvent = useCallback((event: UIStreamEvent) => {
    if (event.event !== 'assistant.delta' && event.event !== 'reasoning.delta') {
      flushDeltaBuffer();
    }

    if (event.conversation_id) {
      setConversationId(event.conversation_id);
    }

    switch (event.event) {
      case 'session.started': {
        const summary = readConversationSummary(event);
        if (summary) {
          setConversations((current) => upsertConversation(current, summary));
        }
        break;
      }
      case 'attachments.imported': {
        const attachments = event.data.attachments;
        const names = Array.isArray(attachments)
          ? attachments.map((item) => {
              if (typeof item === 'object' && item && 'display_name' in item) {
                return String((item as {display_name: unknown}).display_name);
              }
              return '';
            }).filter(Boolean).join(', ')
          : '';
        setBlocks((current) => insertBeforeBlock(current, pendingAssistantBlockIdRef.current, {
          id: `attach-${event.run_id ?? Date.now()}`,
          kind: 'meta',
          label: '附件',
          text: names ? `已导入：${names}` : '已导入附件',
        }));
        break;
      }
      case 'user.accepted': {
        const acceptedBlock: TranscriptBlock = {
          id: event.message_id ?? `user-${Date.now()}`,
          kind: 'user',
          label: '你',
          text: eventText(event, 'content') || pendingPromptRef.current,
          messageId: event.message_id ?? null,
        };
        const pendingUserId = pendingUserBlockIdRef.current;
        setBlocks((current) => {
          if (pendingUserId && current.some((block) => block.id === pendingUserId)) {
            return current.map((block) => block.id === pendingUserId ? {...block, ...acceptedBlock} : block);
          }
          if (current.some((block) => block.id === acceptedBlock.id)) {
            return current.map((block) => block.id === acceptedBlock.id ? {...block, ...acceptedBlock} : block);
          }
          return insertBeforeBlock(current, pendingAssistantBlockIdRef.current, acceptedBlock);
        });
        pendingUserBlockIdRef.current = null;
        pendingPromptRef.current = '';
        break;
      }
      case 'assistant.delta':
        if (pendingAssistantBlockIdRef.current) {
          const pendingId = pendingAssistantBlockIdRef.current;
          setBlocks((current) => current.filter((block) => block.id !== pendingId));
          pendingAssistantBlockIdRef.current = null;
        }
        queueDelta('assistant', event.block_id ?? `assistant-${Date.now()}`, eventText(event, 'text'));
        break;
      case 'reasoning.started':
        setBlocks((current) => [...current, {
          id: event.block_id ?? `reasoning-${Date.now()}`,
          kind: 'reasoning',
          label: '思考过程',
          text: '',
          status: 'running',
          collapsible: true,
          collapsed: false,
        }]);
        break;
      case 'reasoning.delta':
        queueDelta('reasoning', event.block_id ?? `reasoning-${Date.now()}`, eventText(event, 'text'));
        break;
      case 'reasoning.completed':
        if (event.block_id) {
          setBlocks((current) => appendOrUpdate(
            current,
            event.block_id as string,
            {
              id: event.block_id as string,
              kind: 'reasoning',
              label: '思考过程',
              text: '',
              status: 'ok',
              collapsible: true,
              collapsed: true,
            },
            (block) => ({...block, status: 'ok', collapsed: true}),
          ));
        }
        break;
      case 'tool.started':
        setBlocks((current) => [...current, {
          id: event.block_id ?? `tool-${Date.now()}`,
          kind: 'tool',
          label: typeof event.data.name === 'string' ? event.data.name : 'tool',
          text: eventText(event, 'summary'),
          summary: eventText(event, 'summary'),
          status: 'running',
          collapsible: true,
          collapsed: false,
          params: typeof event.data.params === 'object' && event.data.params ? event.data.params as Record<string, unknown> : undefined,
        }]);
        break;
      case 'tool.completed': {
        const blockId = event.block_id ?? `tool-${Date.now()}`;
        const detail = eventText(event, 'detail');
        const statusValue = event.data.status === 'ok' ? 'ok' : 'error';
        const toolResult = Object.prototype.hasOwnProperty.call(event.data, 'result') ? event.data.result : undefined;
        setBlocks((current) => appendOrUpdate(
          current,
          blockId,
          {
            id: blockId,
            kind: 'tool',
            label: typeof event.data.name === 'string' ? event.data.name : 'tool',
            text: detail,
            summary: eventText(event, 'summary'),
            toolResult,
            status: statusValue,
            collapsible: true,
            collapsed: true,
            elapsed: typeof event.data.elapsed === 'number' ? event.data.elapsed : undefined,
          },
          (block) => ({
            ...block,
            text: detail || block.text,
            summary: eventText(event, 'summary') || block.summary,
            toolResult: toolResult ?? block.toolResult,
            status: statusValue,
            elapsed: typeof event.data.elapsed === 'number' ? event.data.elapsed : block.elapsed,
            collapsed: (detail || block.text).length > 120 || (detail || block.text).includes('\n'),
          }),
        ));
        break;
      }
      case 'assistant.completed': {
        const blockId = event.block_id;
        const text = eventText(event, 'text');
        const pendingId = pendingAssistantBlockIdRef.current;
        setBlocks((current) => {
          let next = pendingId ? current.filter((block) => block.id !== pendingId || block.text) : current;
          if (blockId && (text || next.some((block) => block.id === blockId))) {
            next = appendOrUpdate(
              next,
              blockId,
              {
                id: blockId,
                kind: 'assistant',
                label: 'assistant',
                text,
                status: 'ok',
              },
              (block) => ({...block, text: text || block.text, status: 'ok', placeholder: false}),
            );
          }
          return next;
        });
        pendingAssistantBlockIdRef.current = null;
        break;
      }
      case 'conversation.updated': {
        const summary = readConversationSummary(event);
        if (summary) {
          setConversations((current) => upsertConversation(current, summary));
        }
        break;
      }
      case 'session.completed':
        setBusy(false);
        void (async () => {
          await refreshConversations(event.conversation_id, conversationFilter);
          if (event.conversation_id && event.conversation_id === conversationId) {
            const messages = await fetchMessages(event.conversation_id);
            setBlocks(buildBlocksFromMessages(messages));
          }
        })();
        break;
      case 'error':
        if (pendingAssistantBlockIdRef.current) {
          const pendingId = pendingAssistantBlockIdRef.current;
          setBlocks((current) => current.filter((block) => block.id !== pendingId));
          pendingAssistantBlockIdRef.current = null;
        }
        setBlocks((current) => [...current, {
          id: `error-${Date.now()}`,
          kind: 'error',
          label: '错误',
          text: eventText(event, 'message') || '请求失败',
          status: 'error',
        }]);
        setBusy(false);
        break;
      default:
        break;
    }
  }, [conversationFilter, conversationId, flushDeltaBuffer, queueDelta, refreshConversations]);

  const sendMessage = async () => {
    const prompt = input.trim() || (attachedPaths.length ? '请分析这些附件。' : '');
    if (!prompt || busy) {
      return;
    }
    const attachmentBlock = attachedPaths.length
      ? `\n\n[工作区附件]\n${attachedPaths.map((path) => `- ${path}`).join('\n')}`
      : '';
    const message = `${prompt}${attachmentBlock}`;

    const controller = new AbortController();
    const localId = Date.now();
    const pendingUserId = `pending-user-${localId}`;
    const pendingAssistantId = `pending-assistant-${localId}`;
    const activeEdit = editingMessage;
    abortRef.current = controller;
    pendingUserBlockIdRef.current = activeEdit?.messageId ?? pendingUserId;
    pendingAssistantBlockIdRef.current = pendingAssistantId;
    pendingPromptRef.current = message;
    setInput('');
    setAttachedPaths([]);
    setEditingMessage(null);
    setBusy(true);
    setError('');
    if (activeEdit?.messageId) {
      setBlocks((current) => {
        const userIndex = current.findIndex((block) => block.id === activeEdit.messageId);
        const trimmed = userIndex >= 0 ? current.slice(0, userIndex + 1) : current;
        const next = trimmed.map((block) => (
          block.id === activeEdit.messageId ? {...block, text: message} : block
        ));
        return [...next, {
          id: pendingAssistantId,
          kind: 'assistant',
          label: 'assistant',
          text: '',
          status: 'running',
          placeholder: true,
        }];
      });
    } else {
      setBlocks((current) => [...current, {
        id: pendingUserId,
        kind: 'user',
        label: '你',
        text: message,
      }, {
        id: pendingAssistantId,
        kind: 'assistant',
        label: 'assistant',
        text: '',
        status: 'running',
        placeholder: true,
      }]);
    }

    try {
      if (activeEdit?.messageId && conversationId) {
        await streamEditMessage(
          conversationId,
          activeEdit.messageId,
          message,
          {signal: controller.signal},
          applyEvent,
        );
      } else {
        await streamChat(
          {
            message,
            conversation_id: conversationId,
            title: conversationId ? undefined : '新对话',
            provider_id: selectedModel?.provider_id,
            model: selectedModel?.name,
          },
          applyEvent,
          {signal: controller.signal},
        );
      }
    } catch (err) {
      const pendingAssistantId = pendingAssistantBlockIdRef.current;
      if (pendingAssistantId) {
        setBlocks((current) => current.filter((block) => block.id !== pendingAssistantId));
        pendingAssistantBlockIdRef.current = null;
      }
      if ((err as DOMException).name === 'AbortError') {
        setBlocks((current) => [...current, {
          id: `abort-${Date.now()}`,
          kind: 'meta',
          label: '已中止',
          text: '本次生成已停止。',
        }]);
      } else {
        const messageText = err instanceof Error ? err.message : String(err);
        setError(messageText);
        if (activeEdit) {
          setEditingMessage(activeEdit);
          setInput(message);
        }
        setBlocks((current) => [...current, {
          id: `error-${Date.now()}`,
          kind: 'error',
          label: '错误',
          text: messageText,
          status: 'error',
        }]);
      }
    } finally {
      flushDeltaBuffer();
      setBusy(false);
      abortRef.current = null;
      pendingPromptRef.current = '';
      pendingUserBlockIdRef.current = null;
    }
  };

  const stopGeneration = () => {
    abortRef.current?.abort();
  };

  const renameConversation = async (item: ConversationSummary) => {
    const title = window.prompt('重命名会话', item.title)?.trim();
    if (!title || title === item.title) {
      return;
    }
    const updated = await updateConversationTitle(item.id, title);
    setConversations((current) => upsertConversation(current, updated));
  };

  const removeConversation = async (item: ConversationSummary) => {
    if (!window.confirm(`删除会话「${item.title}」？`)) {
      return;
    }
    await deleteConversation(item.id);
    const next = await refreshConversations(undefined, conversationFilter);
    if (conversationId === item.id) {
      const first = next.find((conversation) => conversation.id !== item.id) ?? next[0];
      if (first) {
        await openConversation(first.id);
      } else {
        resetConversation();
      }
    }
  };

  const removeMessage = useCallback(async (messageId: string) => {
    if (!conversationId) {
      return;
    }
    if (!window.confirm('删除此消息？')) {
      return;
    }
    try {
      await deleteMessage(conversationId, messageId);
      setBlocks((current) => current.filter((block) => block.messageId !== messageId));
      setEditingMessage((current) => current?.messageId === messageId ? null : current);
      showToast('已删除消息');
    } catch (err) {
      const messageText = err instanceof Error ? err.message : String(err);
      setError(messageText);
    }
  }, [conversationId, showToast]);

  const regenerateLast = useCallback(async () => {
    if (!conversationId || busy) {
      return;
    }
    // Find the most recent user block with a real messageId.
    const lastUserBlock = [...blocks].reverse().find(
      (block) => block.kind === 'user' && block.messageId,
    );
    if (!lastUserBlock) {
      showToast('没有可重新生成的消息');
      return;
    }
    flushDeltaBuffer();
    // Drop everything visually after that user block.
    const userIndex = blocks.findIndex((block) => block.id === lastUserBlock.id);
    const trimmed = userIndex >= 0 ? blocks.slice(0, userIndex + 1) : blocks;
    const localId = Date.now();
    const pendingAssistantId = `pending-assistant-${localId}`;
    pendingAssistantBlockIdRef.current = pendingAssistantId;
    setBlocks([
      ...trimmed,
      {
        id: pendingAssistantId,
        kind: 'assistant',
        label: 'assistant',
        text: '',
        status: 'running',
        placeholder: true,
      },
    ]);

    const controller = new AbortController();
    abortRef.current = controller;
    setBusy(true);
    setError('');
    try {
      await streamRegenerate(
        conversationId,
        {messageId: lastUserBlock.messageId, signal: controller.signal},
        applyEvent,
      );
    } catch (err) {
      const pendingId = pendingAssistantBlockIdRef.current;
      if (pendingId) {
        setBlocks((current) => current.filter((block) => block.id !== pendingId));
        pendingAssistantBlockIdRef.current = null;
      }
      if ((err as DOMException).name === 'AbortError') {
        setBlocks((current) => [...current, {
          id: `abort-${Date.now()}`,
          kind: 'meta',
          label: '已中止',
          text: '本次生成已停止。',
        }]);
      } else {
        const messageText = err instanceof Error ? err.message : String(err);
        setError(messageText);
        setBlocks((current) => [...current, {
          id: `error-${Date.now()}`,
          kind: 'error',
          label: '错误',
          text: messageText,
          status: 'error',
        }]);
      }
    } finally {
      flushDeltaBuffer();
      setBusy(false);
      abortRef.current = null;
    }
  }, [applyEvent, blocks, busy, conversationId, flushDeltaBuffer, showToast]);

  const switchAssistantVersion = useCallback(async (messageId: string, versionNumber: number) => {
    if (!conversationId || busy) {
      return;
    }
    setBusy(true);
    setError('');
    try {
      const messages = await activateMessageVersion(conversationId, messageId, versionNumber);
      setBlocks(buildBlocksFromMessages(messages));
      await refreshConversations(conversationId, conversationFilter);
      showToast(`已切换到版本 ${versionNumber}`);
    } catch (err) {
      const messageText = err instanceof Error ? err.message : String(err);
      setError(messageText);
    } finally {
      setBusy(false);
    }
  }, [busy, conversationFilter, conversationId, refreshConversations, showToast]);

  const exportConversation = useCallback(async (conversation: ConversationSummary, format: ExportFormat = 'markdown') => {
    try {
      const {filename, blob} = await downloadConversation(conversation.id, format);
      const url = URL.createObjectURL(blob);
      const link = document.createElement('a');
      link.href = url;
      link.download = filename;
      document.body.appendChild(link);
      link.click();
      document.body.removeChild(link);
      URL.revokeObjectURL(url);
      setExportMenuOpen(false);
      showToast(`已导出 ${filename}`);
    } catch (err) {
      const messageText = err instanceof Error ? err.message : String(err);
      setError(messageText);
    }
  }, [showToast]);

  useEffect(() => {
    window.localStorage.setItem(APPEARANCE_STORAGE_KEY, JSON.stringify(effectiveAppearance));
  }, [effectiveAppearance]);

  useEffect(() => {
    if (!exportMenuOpen) {
      return;
    }
    const handlePointerDown = (event: PointerEvent) => {
      const target = event.target as Node | null;
      if (exportMenuRef.current && target && !exportMenuRef.current.contains(target)) {
        setExportMenuOpen(false);
      }
    };
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        setExportMenuOpen(false);
      }
    };
    window.addEventListener('pointerdown', handlePointerDown);
    window.addEventListener('keydown', handleKeyDown);
    return () => {
      window.removeEventListener('pointerdown', handlePointerDown);
      window.removeEventListener('keydown', handleKeyDown);
    };
  }, [exportMenuOpen]);

  useEffect(() => {
    void (async () => {
      try {
        const [nextStatus, nextModels, nextProviders, nextConversations] = await Promise.all([
          fetchStatus(),
          fetchModels(),
          fetchProviders(),
          fetchConversations(conversationFilter),
        ]);
        setStatus(nextStatus);
        setModels(nextModels);
        setProviders(nextProviders);
        setSelectedModelId(nextModels.find((model) => model.default)?.id ?? nextModels[0]?.id ?? '');
        setConversations(nextConversations);
        if (nextConversations[0]) {
          await openConversation(nextConversations[0].id);
        }
      } catch (err) {
        setError(err instanceof Error ? err.message : String(err));
      } finally {
        setLoading(false);
      }
    })();
  }, [conversationFilter, openConversation]);

  useEffect(() => {
    if (loading) {
      return;
    }
    const handle = window.setTimeout(() => {
      void refreshConversations(conversationId, conversationFilter);
    }, 180);
    return () => window.clearTimeout(handle);
  }, [conversationFilter, conversationId, loading, refreshConversations]);

  useEffect(() => {
    return () => {
      if (deltaTimerRef.current) {
        window.clearTimeout(deltaTimerRef.current);
      }
      abortRef.current?.abort();
    };
  }, []);

  useEffect(() => {
    if (suppressNextAutoScrollRef.current) {
      suppressNextAutoScrollRef.current = false;
      return;
    }
    transcriptRef.current?.scrollTo({top: transcriptRef.current.scrollHeight, behavior: 'smooth'});
  }, [blocks, busy]);

  return (
    <div
      className={`app-shell${sidebarCollapsed ? ' sidebar-collapsed' : ''}${inspectorCollapsed ? ' inspector-collapsed' : ''}`}
      style={shellStyle}
    >
      <aside className="sidebar">
        <div className="brand">
          <div className="brand-mark">LA</div>
          <div>
            <strong>Local AI Agent</strong>
            <span>Web Chat</span>
          </div>
          <button type="button" className="ghost-button tiny panel-collapse-button" onClick={() => setSidebarCollapsed(true)}>收起</button>
        </div>
        <button type="button" className="primary-button" onClick={resetConversation}>+ 新建会话</button>
        <input
          type="search"
          className="conversation-search"
          placeholder="搜索会话或消息..."
          value={conversationFilter}
          onChange={(event) => setConversationFilter(event.target.value)}
        />
        <div className="conversation-list">
          {conversations.map((item) => (
            <ConversationItem
              key={item.id}
              item={item}
              active={item.id === conversationId}
              onOpen={() => void openConversation(item.id)}
              onRename={() => void renameConversation(item)}
              onDelete={() => void removeConversation(item)}
              onExport={() => void exportConversation(item, 'markdown')}
            />
          ))}
          {conversations.length === 0 ? (
            <div className="conversation-empty">{conversationFilter ? '没有匹配的会话或消息' : '尚未创建会话'}</div>
          ) : null}
        </div>
      </aside>

      {sidebarCollapsed ? (
        <button
          type="button"
          className="panel-restore panel-restore-left"
          onClick={() => setSidebarCollapsed(false)}
          aria-label="展开会话侧栏"
        >
          会话
        </button>
      ) : null}

      {!sidebarCollapsed ? (
        <button
          type="button"
          className="layout-resizer sidebar-resizer"
          aria-label="拖拽调整会话侧栏宽度"
          onPointerDown={(event) => startResize('sidebar', event)}
        />
      ) : null}

      <main className="chat-panel">
        <header className="topbar">
          <div>
            <strong>{conversationId ? conversations.find((item) => item.id === conversationId)?.title ?? '当前会话' : '新对话'}</strong>
            <span>{status ? `BFF ${status.status} · ${status.workspace_path}` : '正在连接后端...'}</span>
          </div>
          <div className="topbar-actions">
            <ModelPicker models={models} value={selectedModelId} onChange={setSelectedModelId} />
            {conversationId ? (
              <div className="export-menu" ref={exportMenuRef}>
                <button
                  type="button"
                  className="ghost-button tiny"
                  title="导出当前会话"
                  aria-haspopup="menu"
                  aria-expanded={exportMenuOpen}
                  onClick={() => setExportMenuOpen((current) => !current)}
                >导出</button>
                {exportMenuOpen ? (
                  <div className="export-menu-panel" role="menu">
                    <button
                      type="button"
                      role="menuitem"
                      onClick={() => {
                        const current = conversations.find((item) => item.id === conversationId);
                        if (current) {
                          void exportConversation(current, 'markdown');
                        }
                      }}
                    >Markdown</button>
                    <button
                      type="button"
                      role="menuitem"
                      onClick={() => {
                        const current = conversations.find((item) => item.id === conversationId);
                        if (current) {
                          void exportConversation(current, 'json');
                        }
                      }}
                    >JSON</button>
                    <button
                      type="button"
                      role="menuitem"
                      onClick={() => {
                        const current = conversations.find((item) => item.id === conversationId);
                        if (current) {
                          void exportConversation(current, 'txt');
                        }
                      }}
                    >纯文本</button>
                  </div>
                ) : null}
              </div>
            ) : null}
          </div>
        </header>

        <section className="transcript" ref={transcriptRef}>
          {loading ? <div className="loading-card">正在加载...</div> : null}
          {!loading && blocks.length === 0 ? <EmptyState /> : null}
          {transcriptItems.map((item) => (
            item.kind === 'assistant-group'
              ? <AssistantTranscriptItem
                  key={item.id}
                  group={item}
                  onToggle={toggleBlock}
                  onCopy={(text) => void handleCopy(text)}
                  onRegenerate={() => void regenerateLast()}
                  onSwitchVersion={(messageId, versionNumber) => void switchAssistantVersion(messageId, versionNumber)}
                  onDeleteMessage={(messageId) => void removeMessage(messageId)}
                  onOpenConversation={(targetId) => void openConversation(targetId)}
                  busy={busy}
                />
              : <TranscriptItem
                  key={item.id}
                  block={item}
                  onToggle={toggleBlock}
                  onCopy={(text) => void handleCopy(text)}
                  onEditMessage={beginEditMessage}
                  onDeleteMessage={(messageId) => void removeMessage(messageId)}
                  onOpenConversation={(targetId) => void openConversation(targetId)}
                  busy={busy}
                />
          ))}
        </section>

        {error ? <div className="error-banner">{error}</div> : null}

        <footer className="composer-card">
          {editingMessage ? (
            <div className="composer-mode">
              <div>
                <strong>编辑消息</strong>
                <span>发送后将覆盖这条用户消息，并重新生成它后面的回答。</span>
              </div>
              <button type="button" className="ghost-button tiny" onClick={cancelEditMessage}>取消编辑</button>
            </div>
          ) : null}
          {attachedPaths.length ? (
            <div className="attached-files">
              <span>附件</span>
              {attachedPaths.map((path) => (
                <button type="button" key={path} onClick={() => detachWorkspacePath(path)} title="从本次对话移除">
                  {path} ×
                </button>
              ))}
            </div>
          ) : null}
          <textarea
            value={input}
            placeholder={editingMessage
              ? '编辑这条用户消息。发送后会重新生成后续回答。'
              : '输入消息。也可以从右侧工作区上传/附加文件。Enter 发送，Shift+Enter 换行。'}
            onChange={(event) => setInput(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === 'Enter' && !event.shiftKey) {
                event.preventDefault();
                void sendMessage();
              }
            }}
            disabled={busy}
          />
          <div className="composer-actions">
            <span>{busy ? '正在生成...' : selectedModel ? `${selectedModel.provider_name} · ${selectedModel.name}` : '未选择模型'}</span>
            <div>
              {busy ? (
                <button type="button" className="secondary-button" onClick={stopGeneration}>停止</button>
              ) : null}
              <button type="button" className="primary-button" disabled={busy || (!input.trim() && attachedPaths.length === 0)} onClick={() => void sendMessage()}>
                {editingMessage ? '保存并重试' : '发送'}
              </button>
            </div>
          </div>
        </footer>
      </main>

      <aside className="inspector">
        <button type="button" className="ghost-button tiny inspector-collapse-button" onClick={() => setInspectorCollapsed(true)}>收起工作区</button>
        <WorkspacePanel
          attachedPaths={attachedPaths}
          onAttach={attachWorkspacePath}
          onDetach={detachWorkspacePath}
          onError={setError}
        />
        <AppearanceSettingsPanel value={effectiveAppearance} onChange={updateAppearance} onReset={resetAppearance} />
        <section>
          <h2>模型</h2>
          {selectedModel ? (
            <div className="model-card">
              <strong>{selectedModel.name}</strong>
              <span>{selectedModel.provider_name}</span>
              <div className="tag-row">
                {selectedModel.capabilities.map((capability) => <span key={capability}>{capability}</span>)}
              </div>
            </div>
          ) : <p>暂无模型</p>}
        </section>
        <section>
          <h2>Provider</h2>
          {providers.map((provider) => (
            <div key={provider.id} className="provider-row">
              <span>{provider.name}</span>
              <em>{provider.kind}</em>
            </div>
          ))}
        </section>
        <section>
          <h2>工具</h2>
          <div className="tool-cloud">
            {status?.tools.map((tool) => <span key={tool}>{tool}</span>)}
          </div>
        </section>
        <section>
          <h2>状态</h2>
          <p>WebSearch：{status?.websearch_enabled ? '已启用' : '未启用'}</p>
          <p>工具数：{status?.tools.length ?? 0}</p>
        </section>
      </aside>

      {inspectorCollapsed ? (
        <button
          type="button"
          className="panel-restore panel-restore-right"
          onClick={() => setInspectorCollapsed(false)}
          aria-label="展开工作区侧栏"
        >
          工作区
        </button>
      ) : null}

      {!inspectorCollapsed ? (
        <button
          type="button"
          className="layout-resizer inspector-resizer"
          aria-label="拖拽调整右侧工作区宽度"
          onPointerDown={(event) => startResize('inspector', event)}
        />
      ) : null}

      {toast ? <div className="toast" role="status">{toast}</div> : null}
    </div>
  );
}
