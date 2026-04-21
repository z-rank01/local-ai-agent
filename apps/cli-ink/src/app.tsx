import React, {useEffect, useState} from 'react';
import {Box, Text, useApp, useInput} from 'ink';
import TextInput from 'ink-text-input';

import {
  fetchConversations,
  fetchMessages,
  fetchStatus,
  streamChat,
} from './api-client.js';
import {MarkdownMessage} from './markdown.js';
import type {
  AppStatus,
  ConversationSummary,
  MessageRecord,
  UIStreamEvent,
} from './types.js';

type TranscriptEntry = {
  id: string;
  kind: 'meta' | 'user' | 'assistant' | 'reasoning' | 'tool' | 'error';
  label: string;
  text: string;
  status?: 'running' | 'ok' | 'error';
  collapsible?: boolean;
  collapsed?: boolean;
};

type EntryPalette = {
  marker: string;
  markerColor: string;
  labelColor: string;
  textColor: string;
};

const PALETTE = {
  title: '#9fc6d8',
  headerIdle: '#89b4c7',
  headerBusy: '#d9b28c',
  drawerBorder: '#8fa7b8',
  assistantText: '#f4f1ea',
  assistantLabel: '#ddd6cf',
  assistantDot: '#d1c8bf',
  assistantAccent: '#e4d5c5',
  userText: '#b7d3dc',
  userLabel: '#a8c6d1',
  userDot: '#a8c6d1',
  reasoningText: '#707783',
  reasoningLabel: '#808894',
  reasoningDot: '#8d95a2',
  toolText: '#7f8794',
  toolIdle: '#bda9d0',
  toolOk: '#adc7a0',
  toolError: '#e3a4a7',
  metaText: '#8f98a4',
  errorText: '#f0a3a6',
  focus: '#f2d6a2',
} as const;

function appendOrUpdate(
  entries: TranscriptEntry[],
  targetId: string,
  fallback: TranscriptEntry,
  updater: (current: TranscriptEntry) => TranscriptEntry,
): TranscriptEntry[] {
  const index = entries.findIndex((entry) => entry.id === targetId);
  if (index === -1) {
    return [...entries, updater(fallback)];
  }
  return entries.map((entry, currentIndex) => {
    if (currentIndex !== index) {
      return entry;
    }
    return updater(entry);
  });
}

function eventText(event: UIStreamEvent, key: string): string {
  const value = event.data[key];
  return typeof value === 'string' ? value : '';
}

function summarizeText(text: string, limit = 132): string {
  const normalized = text.replace(/\s+/g, ' ').trim();
  if (!normalized) {
    return '';
  }
  return normalized.length > limit ? `${normalized.slice(0, limit - 1)}…` : normalized;
}

function truncateText(text: string, limit = 26): string {
  if (text.length <= limit) {
    return text;
  }
  return `${text.slice(0, limit - 1)}…`;
}

function stripPlaceholder(entries: TranscriptEntry[]): TranscriptEntry[] {
  return entries.filter((entry) => entry.id !== 'empty-state');
}

function isCollapsible(entry: TranscriptEntry): boolean {
  return Boolean(entry.collapsible);
}

function nextCollapsibleId(
  currentId: string | null,
  entries: TranscriptEntry[],
): string | null {
  const ids = entries.filter(isCollapsible).map((entry) => entry.id);
  if (!ids.length) {
    return null;
  }
  if (!currentId) {
    return ids[0];
  }
  const currentIndex = ids.indexOf(currentId);
  if (currentIndex === -1) {
    return ids[0];
  }
  return ids[(currentIndex + 1) % ids.length];
}

function formatConversationStamp(value: string): string {
  const date = new Date(value);
  if (Number.isNaN(date.valueOf())) {
    return '';
  }
  return date.toLocaleString('zh-CN', {
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  });
}

function inferToolStatus(text: string): 'ok' | 'error' {
  const normalized = text.trim().toLowerCase();
  return normalized.startsWith('[ok]') ? 'ok' : 'error';
}

function buildBlankEntries(hasHistory: boolean): TranscriptEntry[] {
  return [
    {
      id: 'empty-state',
      kind: 'meta',
      label: 'system',
      text: hasHistory
        ? 'Ready for a new conversation. Press Ctrl+O to browse history or start typing.'
        : 'Ready. Type a prompt to start your first conversation.',
    },
  ];
}

function buildEntriesFromMessages(messages: MessageRecord[]): TranscriptEntry[] {
  if (!messages.length) {
    return buildBlankEntries(true);
  }

  const entries: TranscriptEntry[] = [];

  for (const message of messages) {
    if (message.role === 'user') {
      entries.push({
        id: message.id,
        kind: 'user',
        label: 'you',
        text: message.content,
      });
      continue;
    }

    if (message.role === 'assistant') {
      if (message.thinking.trim()) {
        entries.push({
          id: `${message.id}-thinking`,
          kind: 'reasoning',
          label: 'thinking',
          text: message.thinking,
          status: 'ok',
          collapsible: true,
          collapsed: true,
        });
      }

      if (message.content.trim()) {
        entries.push({
          id: message.id,
          kind: 'assistant',
          label: 'assistant',
          text: message.content,
        });
      }
      continue;
    }

    if (message.role === 'tool') {
      entries.push({
        id: message.id,
        kind: 'tool',
        label: message.tool_name || 'tool',
        text: message.content,
        status: inferToolStatus(message.content),
        collapsible: true,
        collapsed: message.content.includes('\n') || message.content.length > 120,
      });
      continue;
    }

    if (message.content.trim()) {
      entries.push({
        id: message.id,
        kind: 'meta',
        label: message.role,
        text: message.content,
      });
    }
  }

  return entries.length ? entries : buildBlankEntries(true);
}

function entryPalette(entry: TranscriptEntry): EntryPalette {
  switch (entry.kind) {
    case 'assistant':
      return {
        marker: '◆',
        markerColor: PALETTE.assistantDot,
        labelColor: PALETTE.assistantLabel,
        textColor: PALETTE.assistantText,
      };

    case 'user':
      return {
        marker: '◦',
        markerColor: PALETTE.userDot,
        labelColor: PALETTE.userLabel,
        textColor: PALETTE.userText,
      };

    case 'reasoning':
      return {
        marker: '●',
        markerColor: PALETTE.reasoningDot,
        labelColor: PALETTE.reasoningLabel,
        textColor: PALETTE.reasoningText,
      };

    case 'tool': {
      const color =
        entry.status === 'error'
          ? PALETTE.toolError
          : entry.status === 'ok'
            ? PALETTE.toolOk
            : PALETTE.toolIdle;
      return {
        marker: '●',
        markerColor: color,
        labelColor: color,
        textColor: PALETTE.toolText,
      };
    }

    case 'error':
      return {
        marker: '●',
        markerColor: PALETTE.errorText,
        labelColor: PALETTE.errorText,
        textColor: PALETTE.errorText,
      };

    default:
      return {
        marker: '·',
        markerColor: PALETTE.metaText,
        labelColor: PALETTE.metaText,
        textColor: PALETTE.metaText,
      };
  }
}

function TranscriptEntryView({
  entry,
  isSelected,
}: {
  entry: TranscriptEntry;
  isSelected: boolean;
}) {
  const palette = entryPalette(entry);
  const preview = summarizeText(entry.text);
  const toggleIcon = entry.collapsible ? (entry.collapsed ? '▸' : '▾') : ' ';

  return (
    <Box flexDirection="column" marginBottom={1}>
      <Box>
        <Text color={isSelected ? PALETTE.focus : palette.markerColor}>{toggleIcon}</Text>
        <Text color={palette.markerColor}>{` ${palette.marker}`}</Text>
        <Text color={palette.labelColor}>{` ${entry.label}`}</Text>
        {entry.status === 'running' ? (
          <Text color={PALETTE.metaText}>  live</Text>
        ) : entry.collapsible ? (
          <Text color={PALETTE.metaText}>{entry.collapsed ? '  folded' : '  open'}</Text>
        ) : null}
      </Box>

      <Box marginLeft={4} flexDirection="column">
        {entry.collapsible && entry.collapsed ? (
          <Text color={palette.textColor}>{preview || '...'}</Text>
        ) : entry.kind === 'assistant' ? (
          <MarkdownMessage
            content={entry.text}
            palette={{
              text: palette.textColor,
              muted: PALETTE.metaText,
              accent: PALETTE.assistantAccent,
              link: PALETTE.userText,
              code: PALETTE.assistantAccent,
              quote: PALETTE.metaText,
            }}
          />
        ) : (
          <Text color={palette.textColor}>{entry.text || (entry.status === 'running' ? '...' : '')}</Text>
        )}
      </Box>
    </Box>
  );
}

function HistoryDrawer({
  conversations,
  currentConversationId,
  selectedIndex,
}: {
  conversations: ConversationSummary[];
  currentConversationId: string | null;
  selectedIndex: number;
}) {
  const maxVisible = 10;
  const start = Math.max(0, Math.min(selectedIndex - 4, Math.max(0, conversations.length - maxVisible)));
  const visible = conversations.slice(start, start + maxVisible);

  return (
    <Box
      width={38}
      marginLeft={1}
      borderStyle="round"
      borderColor={PALETTE.drawerBorder}
      paddingX={1}
      flexDirection="column"
    >
      <Text color={PALETTE.title}>Conversation History</Text>
      <Text color={PALETTE.metaText}>Up/Down move · Ctrl+L load · Ctrl+N new</Text>

      <Box flexDirection="column" marginTop={1}>
        {visible.map((conversation, offset) => {
          const absoluteIndex = start + offset;
          const selected = absoluteIndex === selectedIndex;
          const current = conversation.id === currentConversationId;

          return (
            <Box key={conversation.id} justifyContent="space-between">
              <Box>
                <Text color={selected ? PALETTE.focus : PALETTE.metaText}>{selected ? '›' : ' '}</Text>
                <Text color={current ? PALETTE.assistantDot : PALETTE.userDot}>{current ? '●' : '·'}</Text>
                <Text color={selected || current ? PALETTE.assistantText : PALETTE.userText}>{` ${truncateText(conversation.title)}`}</Text>
              </Box>
              <Text color={PALETTE.metaText}>{formatConversationStamp(conversation.updated_at)}</Text>
            </Box>
          );
        })}
      </Box>

      {conversations.length > visible.length ? (
        <Text color={PALETTE.metaText}>{`${visible.length}/${conversations.length} shown`}</Text>
      ) : null}
    </Box>
  );
}

export function ShellApp() {
  const {exit} = useApp();
  const [status, setStatus] = useState<AppStatus | null>(null);
  const [statusError, setStatusError] = useState<string>('');
  const [conversations, setConversations] = useState<ConversationSummary[]>([]);
  const [historyOpen, setHistoryOpen] = useState(false);
  const [historySelection, setHistorySelection] = useState(0);
  const [conversationId, setConversationId] = useState<string | null>(null);
  const [selectedEntryId, setSelectedEntryId] = useState<string | null>(null);
  const [entries, setEntries] = useState<TranscriptEntry[]>(buildBlankEntries(false));
  const [input, setInput] = useState('');
  const [busy, setBusy] = useState(false);

  const refreshConversations = async (preferredId?: string | null): Promise<ConversationSummary[]> => {
    const nextConversations = await fetchConversations();
    setConversations(nextConversations);

    if (!nextConversations.length) {
      setHistorySelection(0);
      return nextConversations;
    }

    const targetId = preferredId ?? conversationId ?? nextConversations[0].id;
    const nextIndex = nextConversations.findIndex((item) => item.id === targetId);
    setHistorySelection(nextIndex >= 0 ? nextIndex : 0);
    return nextConversations;
  };

  const openConversation = async (
    targetId: string,
    knownConversations?: ConversationSummary[],
  ): Promise<void> => {
    const [messages, nextConversations] = await Promise.all([
      fetchMessages(targetId),
      knownConversations ? Promise.resolve(knownConversations) : refreshConversations(targetId),
    ]);

    setConversationId(targetId);
    setEntries(buildEntriesFromMessages(messages));
    setSelectedEntryId(null);
    setHistoryOpen(false);

    const nextIndex = nextConversations.findIndex((item) => item.id === targetId);
    if (nextIndex >= 0) {
      setHistorySelection(nextIndex);
    }
  };

  const resetConversation = () => {
    setConversationId(null);
    setEntries(buildBlankEntries(conversations.length > 0));
    setSelectedEntryId(null);
    setHistoryOpen(false);
    setInput('');
  };

  const toggleSelectedEntry = () => {
    if (!selectedEntryId) {
      return;
    }

    setEntries((current) =>
      current.map((entry) =>
        entry.id === selectedEntryId
          ? {
              ...entry,
              collapsed: entry.collapsible ? !entry.collapsed : entry.collapsed,
            }
          : entry,
      ),
    );
  };

  useInput((inputValue, key) => {
    if (historyOpen && key.escape) {
      setHistoryOpen(false);
      return;
    }

    if (key.escape) {
      exit();
      return;
    }

    if (key.ctrl && inputValue.toLowerCase() === 'o' && !busy) {
      if (conversations.length) {
        setHistoryOpen((current) => !current);
      }
      return;
    }

    if (key.ctrl && inputValue.toLowerCase() === 'n' && !busy) {
      resetConversation();
      return;
    }

    if (key.tab) {
      const nextId = nextCollapsibleId(selectedEntryId, entries);
      if (nextId) {
        setSelectedEntryId(nextId);
      }
      return;
    }

    if (key.ctrl && inputValue.toLowerCase() === 'e') {
      toggleSelectedEntry();
      return;
    }

    if (historyOpen && key.ctrl && inputValue.toLowerCase() === 'l' && !busy) {
      const targetConversation = conversations[historySelection];
      if (targetConversation) {
        void openConversation(targetConversation.id, conversations);
      }
      return;
    }

    if (historyOpen && key.upArrow) {
      setHistorySelection((current) => Math.max(0, current - 1));
      return;
    }

    if (historyOpen && key.downArrow) {
      setHistorySelection((current) => Math.min(conversations.length - 1, current + 1));
    }
  });

  useEffect(() => {
    void (async () => {
      try {
        const nextStatus = await fetchStatus();
        setStatus(nextStatus);

        const nextConversations = await refreshConversations();
        if (nextConversations.length) {
          await openConversation(nextConversations[0].id, nextConversations);
        } else {
          setEntries(buildBlankEntries(false));
        }
      } catch (error) {
        const message = error instanceof Error ? error.message : String(error);
        setStatusError(message);
        setEntries([
          {
            id: 'status-error',
            kind: 'error',
            label: 'adapter',
            text: message,
            status: 'error',
          },
        ]);
      }
    })();
  }, []);

  useEffect(() => {
    const foldableEntries = entries.filter(isCollapsible);
    if (!foldableEntries.length) {
      if (selectedEntryId !== null) {
        setSelectedEntryId(null);
      }
      return;
    }

    if (!selectedEntryId || !foldableEntries.some((entry) => entry.id === selectedEntryId)) {
      setSelectedEntryId(foldableEntries[0].id);
    }
  }, [entries, selectedEntryId]);

  useEffect(() => {
    if (!conversationId) {
      return;
    }

    const nextIndex = conversations.findIndex((item) => item.id === conversationId);
    if (nextIndex >= 0) {
      setHistorySelection(nextIndex);
    }
  }, [conversationId, conversations]);

  const applyEvent = (event: UIStreamEvent) => {
    if (event.conversation_id) {
      setConversationId(event.conversation_id);
    }

    switch (event.event) {
      case 'session.started':
        void refreshConversations(event.conversation_id);
        break;

      case 'attachments.imported': {
        const attachments = event.data.attachments;
        const names = Array.isArray(attachments)
          ? attachments
              .map((item) => {
                if (typeof item === 'object' && item && 'display_name' in item) {
                  return String(item.display_name);
                }
                return '';
              })
              .filter(Boolean)
              .join(', ')
          : '';
        setEntries((current) => [
          ...stripPlaceholder(current),
          {
            id: `attach-${event.run_id}`,
            kind: 'meta',
            label: 'import',
            text: names ? `imported ${names}` : 'attachments imported',
          },
        ]);
        break;
      }

      case 'user.accepted':
        setEntries((current) => [
          ...stripPlaceholder(current),
          {
            id: event.message_id ?? `user-${Date.now()}`,
            kind: 'user',
            label: 'you',
            text: eventText(event, 'content'),
          },
        ]);
        break;

      case 'assistant.delta': {
        const blockId = event.block_id ?? `assistant-${Date.now()}`;
        const delta = eventText(event, 'text');
        setEntries((current) =>
          appendOrUpdate(
            stripPlaceholder(current),
            blockId,
            {
              id: blockId,
              kind: 'assistant',
              label: 'assistant',
              text: '',
            },
            (entry) => ({...entry, text: `${entry.text}${delta}`}),
          ),
        );
        break;
      }

      case 'reasoning.started': {
        const blockId = event.block_id ?? `reasoning-${Date.now()}`;
        setSelectedEntryId(blockId);
        setEntries((current) => [
          ...stripPlaceholder(current),
          {
            id: blockId,
            kind: 'reasoning',
            label: 'thinking',
            text: '',
            status: 'running',
            collapsible: true,
            collapsed: false,
          },
        ]);
        break;
      }

      case 'reasoning.delta': {
        const blockId = event.block_id ?? `reasoning-${Date.now()}`;
        const delta = eventText(event, 'text');
        setEntries((current) =>
          appendOrUpdate(
            stripPlaceholder(current),
            blockId,
            {
              id: blockId,
              kind: 'reasoning',
              label: 'thinking',
              text: '',
              status: 'running',
              collapsible: true,
              collapsed: false,
            },
            (entry) => ({...entry, text: `${entry.text}${delta}`}),
          ),
        );
        break;
      }

      case 'reasoning.completed': {
        const blockId = event.block_id;
        if (blockId) {
          setEntries((current) =>
            appendOrUpdate(
              current,
              blockId,
              {
                id: blockId,
                kind: 'reasoning',
                label: 'thinking',
                text: '',
                status: 'ok',
                collapsible: true,
                collapsed: true,
              },
              (entry) => ({...entry, status: 'ok', collapsed: true}),
            ),
          );
        }
        break;
      }

      case 'tool.started': {
        const blockId = event.block_id ?? `tool-${Date.now()}`;
        const label = typeof event.data.name === 'string' ? event.data.name : 'tool';
        setSelectedEntryId(blockId);
        setEntries((current) => [
          ...stripPlaceholder(current),
          {
            id: blockId,
            kind: 'tool',
            label,
            text: eventText(event, 'summary'),
            status: 'running',
            collapsible: true,
            collapsed: false,
          },
        ]);
        break;
      }

      case 'tool.completed': {
        const blockId = event.block_id ?? `tool-${Date.now()}`;
        const detail = eventText(event, 'detail');
        const statusValue = event.data.status === 'ok' ? 'ok' : 'error';
        setEntries((current) =>
          appendOrUpdate(
            current,
            blockId,
            {
              id: blockId,
              kind: 'tool',
              label: typeof event.data.name === 'string' ? event.data.name : 'tool',
              text: detail,
              status: statusValue,
              collapsible: true,
              collapsed: detail.includes('\n') || detail.length > 120,
            },
            (entry) => ({
              ...entry,
              text: detail || entry.text,
              status: statusValue,
              collapsed: (detail || entry.text).includes('\n') || (detail || entry.text).length > 120,
            }),
          ),
        );
        break;
      }

      case 'conversation.updated':
      case 'assistant.completed':
      case 'session.completed':
        void refreshConversations(event.conversation_id);
        break;

      case 'error':
        setEntries((current) => [
          ...stripPlaceholder(current),
          {
            id: `error-${Date.now()}`,
            kind: 'error',
            label: 'error',
            text: eventText(event, 'message') || 'stream error',
            status: 'error',
          },
        ]);
        break;

      default:
        break;
    }
  };

  const submitPrompt = async () => {
    const prompt = input.trim();
    if (!prompt || busy) {
      return;
    }

    setBusy(true);
    setInput('');
    try {
      await streamChat({message: prompt, conversation_id: conversationId}, applyEvent);
    } catch (error) {
      setEntries((current) => [
        ...stripPlaceholder(current),
        {
          id: `client-error-${Date.now()}`,
          kind: 'error',
          label: 'adapter',
          text: error instanceof Error ? error.message : String(error),
          status: 'error',
        },
      ]);
    } finally {
      setBusy(false);
    }
  };

  const currentConversation = conversations.find((item) => item.id === conversationId) ?? null;
  const activityLabel = busy ? 'streaming' : historyOpen ? 'history' : 'idle';
  const footerText = historyOpen
    ? 'Enter submit · Up/Down move · Ctrl+L load · Ctrl+N new · Esc close'
    : 'Enter submit · Ctrl+O history · Ctrl+N new · Tab select fold · Ctrl+E toggle · Esc exit';

  return (
    <Box flexDirection="column" padding={1}>
      <Box
        borderStyle="round"
        borderColor={busy ? PALETTE.headerBusy : PALETTE.headerIdle}
        paddingX={1}
        paddingY={0}
        flexDirection="column"
      >
        <Box justifyContent="space-between">
          <Text color={PALETTE.title}>Local AI Agent / Ink shell</Text>
          <Text color={busy ? PALETTE.headerBusy : PALETTE.metaText}>{activityLabel}</Text>
        </Box>
        <Box marginTop={1} flexDirection="column">
          <Text color={PALETTE.metaText}>
            {statusError
              ? `adapter offline · ${statusError}`
              : status
                ? `model ${status.model} · workspace ${status.workspace_path}`
                : 'loading adapter status...'}
          </Text>
          <Text color={PALETTE.metaText}>
            {currentConversation
              ? `conversation ${truncateText(currentConversation.title, 40)}`
              : conversationId
                ? `conversation ${conversationId}`
                : 'conversation new'}
          </Text>
        </Box>
      </Box>

      <Box marginTop={1}>
        <Box
          flexDirection="column"
          flexGrow={1}
          borderStyle="round"
          borderColor={historyOpen ? PALETTE.headerIdle : 'gray'}
          paddingX={1}
          minWidth={0}
        >
          {entries.map((entry) => (
            <TranscriptEntryView key={entry.id} entry={entry} isSelected={entry.id === selectedEntryId} />
          ))}
        </Box>

        {historyOpen ? (
          <HistoryDrawer
            conversations={conversations}
            currentConversationId={conversationId}
            selectedIndex={historySelection}
          />
        ) : null}
      </Box>

      <Box
        marginTop={1}
        borderStyle="round"
        borderColor={busy ? PALETTE.headerBusy : PALETTE.headerIdle}
        paddingX={1}
      >
        <Text color={PALETTE.metaText}>prompt  </Text>
        <TextInput value={input} onChange={setInput} onSubmit={submitPrompt} />
      </Box>

      <Box marginTop={1} flexDirection="column">
        <Text color={PALETTE.metaText}>{footerText}</Text>
        <Text color={PALETTE.metaText}>
          {currentConversation ? `current ${currentConversation.id}` : `current ${conversationId ?? 'new'}`}
          {selectedEntryId ? ` · fold target ${selectedEntryId}` : ''}
        </Text>
      </Box>
    </Box>
  );
}