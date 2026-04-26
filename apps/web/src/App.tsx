import {useCallback, useEffect, useMemo, useRef, useState, type CSSProperties, type PointerEvent as ReactPointerEvent} from 'react';
import {
  deleteConversation,
  fetchConversations,
  fetchMessages,
  fetchModels,
  fetchProviders,
  fetchStatus,
  streamChat,
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

type TranscriptViewItem = TranscriptBlock | AssistantTranscriptGroup;

function clamp(value: number, min: number, max: number): number {
  return Math.min(Math.max(value, min), max);
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
      });
      return blocks;
    }

    if (message.role === 'tool') {
      return [{
        id: message.id,
        kind: 'tool' as const,
        label: message.tool_name || 'tool',
        text: message.content,
        messageId: message.id,
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

function AssistantSection({block, onToggle}: {block: TranscriptBlock; onToggle: (id: string) => void}) {
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
    <div className="tool-detail">
      {block.params ? <pre>{JSON.stringify(block.params, null, 2)}</pre> : null}
      {block.text ? <pre>{block.text}</pre> : <LoadingDots label="正在调用工具..." />}
    </div>
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
        {block.collapsible ? (
          <button type="button" className="ghost-button tiny" onClick={() => onToggle(block.id)}>
            {collapsed ? '展开' : '折叠'}
          </button>
        ) : null}
      </header>
      {content}
    </section>
  );
}

function AssistantTranscriptItem({group, onToggle}: {group: AssistantTranscriptGroup; onToggle: (id: string) => void}) {
  const status = assistantGroupStatus(group.blocks);
  const createdAt = group.blocks.find((block) => block.createdAt)?.createdAt;
  const ordered = orderedAssistantBlocks(group.blocks);
  const hasAnswer = ordered.some((block) => block.kind === 'assistant');

  return (
    <article className="message message-assistant message-assistant-group">
      <div className="avatar">AI</div>
      <div className="message-body">
        <header className="message-header">
          <span>assistant</span>
          {status ? <span className={`status-pill status-${status}`}>{status === 'running' ? '生成中' : status}</span> : null}
          {createdAt ? <time>{formatTime(createdAt)}</time> : null}
        </header>
        <div className="assistant-sections">
          {ordered.map((block) => <AssistantSection key={block.id} block={block} onToggle={onToggle} />)}
          {!hasAnswer && status === 'running' ? <LoadingDots label="正在等待模型输出..." /> : null}
        </div>
      </div>
    </article>
  );
}

function TranscriptItem({block, onToggle}: {block: TranscriptBlock; onToggle: (id: string) => void}) {
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
    <div className="tool-detail">
      {block.params ? <pre>{JSON.stringify(block.params, null, 2)}</pre> : null}
      <pre>{block.text}</pre>
    </div>
  ) : (
    <pre className="plain-pre">{block.text}</pre>
  );

  return (
    <article className={`message message-${block.kind}`}>
      <div className="avatar">{icon}</div>
      <div className="message-body">
        <header className="message-header">
          <span>{block.label}</span>
          {block.status ? <span className={`status-pill status-${block.status}`}>{block.status}</span> : null}
          {block.createdAt ? <time>{formatTime(block.createdAt)}</time> : null}
          {block.collapsible ? (
            <button type="button" className="ghost-button tiny" onClick={() => onToggle(block.id)}>
              {block.collapsed ? '展开' : '折叠'}
            </button>
          ) : null}
        </header>
        {content}
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
}: {
  item: ConversationSummary;
  active: boolean;
  onOpen: () => void;
  onRename: () => void;
  onDelete: () => void;
}) {
  return (
    <button type="button" className={`conversation-item${active ? ' active' : ''}`} onClick={onOpen}>
      <span className="conversation-title">{item.title}</span>
      <span className="conversation-meta">{item.model || 'default'} · {formatTime(item.updated_at)}</span>
      <span className="conversation-actions" onClick={(event) => event.stopPropagation()}>
        <button type="button" title="重命名" onClick={onRename}>✎</button>
        <button type="button" title="删除" onClick={onDelete}>×</button>
      </span>
    </button>
  );
}

export default function App() {
  const [status, setStatus] = useState<AppStatus | null>(null);
  const [providers, setProviders] = useState<ProviderInfo[]>([]);
  const [models, setModels] = useState<ModelInfo[]>([]);
  const [selectedModelId, setSelectedModelId] = useState<string>('');
  const [conversations, setConversations] = useState<ConversationSummary[]>([]);
  const [conversationId, setConversationId] = useState<string | null>(null);
  const [blocks, setBlocks] = useState<TranscriptBlock[]>([]);
  const [input, setInput] = useState('');
  const [attachedPaths, setAttachedPaths] = useState<string[]>([]);
  const [sidebarWidth, setSidebarWidth] = useState(300);
  const [inspectorWidth, setInspectorWidth] = useState(360);
  const [sidebarCollapsed, setSidebarCollapsed] = useState(false);
  const [inspectorCollapsed, setInspectorCollapsed] = useState(false);
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

  const selectedModel = useMemo(
    () => models.find((model) => model.id === selectedModelId) ?? models.find((model) => model.default) ?? models[0],
    [models, selectedModelId],
  );

  const transcriptItems = useMemo(() => buildTranscriptViewItems(blocks), [blocks]);

  const shellStyle = useMemo(() => ({
    '--sidebar-width': sidebarCollapsed ? '0px' : `${sidebarWidth}px`,
    '--inspector-width': inspectorCollapsed ? '0px' : `${inspectorWidth}px`,
  }) as CSSProperties, [inspectorCollapsed, inspectorWidth, sidebarCollapsed, sidebarWidth]);

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

  const refreshConversations = useCallback(async (preferredId?: string | null) => {
    const next = await fetchConversations();
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
    setError('');
  };

  const toggleBlock = (id: string) => {
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
        };
        const pendingUserId = pendingUserBlockIdRef.current;
        setBlocks((current) => {
          if (pendingUserId && current.some((block) => block.id === pendingUserId)) {
            return current.map((block) => block.id === pendingUserId ? acceptedBlock : block);
          }
          if (current.some((block) => block.id === acceptedBlock.id)) {
            return current;
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
        setBlocks((current) => appendOrUpdate(
          current,
          blockId,
          {
            id: blockId,
            kind: 'tool',
            label: typeof event.data.name === 'string' ? event.data.name : 'tool',
            text: detail,
            status: statusValue,
            collapsible: true,
            collapsed: true,
          },
          (block) => ({
            ...block,
            text: detail || block.text,
            status: statusValue,
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
        void refreshConversations(event.conversation_id);
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
  }, [flushDeltaBuffer, queueDelta, refreshConversations]);

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
    abortRef.current = controller;
    pendingUserBlockIdRef.current = pendingUserId;
    pendingAssistantBlockIdRef.current = pendingAssistantId;
    pendingPromptRef.current = message;
    setInput('');
    setAttachedPaths([]);
    setBusy(true);
    setError('');
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

    try {
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
    const next = await refreshConversations();
    if (conversationId === item.id) {
      const first = next.find((conversation) => conversation.id !== item.id) ?? next[0];
      if (first) {
        await openConversation(first.id);
      } else {
        resetConversation();
      }
    }
  };

  useEffect(() => {
    void (async () => {
      try {
        const [nextStatus, nextModels, nextProviders, nextConversations] = await Promise.all([
          fetchStatus(),
          fetchModels(),
          fetchProviders(),
          fetchConversations(),
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
  }, [openConversation]);

  useEffect(() => {
    return () => {
      if (deltaTimerRef.current) {
        window.clearTimeout(deltaTimerRef.current);
      }
      abortRef.current?.abort();
    };
  }, []);

  useEffect(() => {
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
        <div className="conversation-list">
          {conversations.map((item) => (
            <ConversationItem
              key={item.id}
              item={item}
              active={item.id === conversationId}
              onOpen={() => void openConversation(item.id)}
              onRename={() => void renameConversation(item)}
              onDelete={() => void removeConversation(item)}
            />
          ))}
        </div>
      </aside>

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
          <div className="layout-toggle-row">
            <button type="button" className="ghost-button tiny" onClick={() => setSidebarCollapsed((value) => !value)}>
              {sidebarCollapsed ? '展开会话' : '收起会话'}
            </button>
            <button type="button" className="ghost-button tiny" onClick={() => setInspectorCollapsed((value) => !value)}>
              {inspectorCollapsed ? '展开工作区' : '收起工作区'}
            </button>
          </div>
          <div>
            <strong>{conversationId ? conversations.find((item) => item.id === conversationId)?.title ?? '当前会话' : '新对话'}</strong>
            <span>{status ? `BFF ${status.status} · ${status.workspace_path}` : '正在连接后端...'}</span>
          </div>
          <select value={selectedModelId} onChange={(event) => setSelectedModelId(event.target.value)}>
            {models.map((model) => (
              <option key={model.id} value={model.id}>{model.provider_name} / {model.name}</option>
            ))}
          </select>
        </header>

        <section className="transcript" ref={transcriptRef}>
          {loading ? <div className="loading-card">正在加载...</div> : null}
          {!loading && blocks.length === 0 ? <EmptyState /> : null}
          {transcriptItems.map((item) => (
            item.kind === 'assistant-group'
              ? <AssistantTranscriptItem key={item.id} group={item} onToggle={toggleBlock} />
              : <TranscriptItem key={item.id} block={item} onToggle={toggleBlock} />
          ))}
        </section>

        {error ? <div className="error-banner">{error}</div> : null}

        <footer className="composer-card">
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
            placeholder="输入消息。也可以从右侧工作区上传/附加文件。Enter 发送，Shift+Enter 换行。"
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
                发送
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

      {!inspectorCollapsed ? (
        <button
          type="button"
          className="layout-resizer inspector-resizer"
          aria-label="拖拽调整右侧工作区宽度"
          onPointerDown={(event) => startResize('inspector', event)}
        />
      ) : null}
    </div>
  );
}
