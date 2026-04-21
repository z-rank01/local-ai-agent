import React, {useEffect, useRef, useState} from 'react';
import {Box, Text, useApp, useInput, useStdout} from 'ink';

import {
  deleteConversation,
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

type BufferedDelta = {
  id: string;
  kind: 'assistant' | 'reasoning';
  text: string;
};

type EntryPalette = {
  dotColor: string;
  guideColor: string;
  labelColor: string;
  textColor: string;
};

const HISTORY_DRAWER_WIDTH = 44;
const HISTORY_DRAWER_MIN_WIDTH = 18;
const TRANSCRIPT_MIN_WIDTH = 28;
const DELTA_FLUSH_MS = 48;

const PALETTE = {
  title: '#9fc6d8',
  headerIdle: '#89b4c7',
  headerBusy: '#d9b28c',
  drawerBorder: '#8fa7b8',
  assistantText: '#f4f1ea',
  assistantLabel: '#ddd6cf',
  assistantDot: '#d1c8bf',
  assistantAccent: '#e4d5c5',
  userText: '#c4ddcb',
  userLabel: '#9dcdab',
  userDot: '#78b68b',
  reasoningText: '#707783',
  reasoningLabel: '#808894',
  reasoningDot: '#a99bc2',
  toolText: '#7f8794',
  toolIdle: '#bda9d0',
  toolOk: '#adc7a0',
  toolError: '#e3a4a7',
  metaText: '#8f98a4',
  errorText: '#f0a3a6',
  focus: '#f2d6a2',
} as const;

function sanitizeSingleLine(text: string): string {
  return text.replace(/\s+/g, ' ').trim();
}

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
  const normalized = sanitizeSingleLine(text);
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

function isConversationSummary(value: unknown): value is ConversationSummary {
  if (!value || typeof value !== 'object') {
    return false;
  }

  const record = value as Record<string, unknown>;
  return (
    typeof record.id === 'string' &&
    typeof record.title === 'string' &&
    typeof record.model === 'string' &&
    typeof record.created_at === 'string' &&
    typeof record.updated_at === 'string'
  );
}

function normalizeConversation(conversation: ConversationSummary): ConversationSummary {
  return {
    ...conversation,
    title: sanitizeSingleLine(conversation.title),
  };
}

function readConversationSummary(event: UIStreamEvent): ConversationSummary | null {
  const candidate = event.data.conversation;
  if (!isConversationSummary(candidate)) {
    return null;
  }

  return normalizeConversation(candidate);
}

function upsertConversation(
  conversations: ConversationSummary[],
  summary: ConversationSummary,
): ConversationSummary[] {
  const normalized = normalizeConversation(summary);
  return [normalized, ...conversations.filter((conversation) => conversation.id !== normalized.id)];
}

function inferToolStatus(text: string): 'ok' | 'error' {
  const normalized = text.trim().toLowerCase();
  return normalized.startsWith('[ok]') ? 'ok' : 'error';
}

function plainEntryText(entry: TranscriptEntry): string {
  if (entry.collapsible && entry.collapsed) {
    return summarizeText(entry.text);
  }
  return entry.text;
}

function gapBeforeEntry(entry: TranscriptEntry, previousEntry?: TranscriptEntry): number {
  if (!previousEntry) {
    return 0;
  }

  return entry.kind === 'user' ? 2 : 1;
}

function countWrappedLines(text: string, width: number, maxLines: number): number {
  const safeWidth = Math.max(18, width);
  const lines = (text || ' ').split('\n');
  let total = 0;

  for (const line of lines) {
    total += Math.max(1, Math.ceil(Math.max(1, line.length) / safeWidth));
    if (total >= maxLines) {
      return maxLines;
    }
  }

  return total;
}

function estimateEntryHeight(entry: TranscriptEntry, contentWidth: number): number {
  const content = plainEntryText(entry);
  const budget = entry.collapsible && !entry.collapsed ? 8 : 3;
  // +1 for header row, +1 for explicit spacer line between header and guide line
  return 2 + countWrappedLines(content, contentWidth, budget);
}

function buildTranscriptViewport(
  entries: TranscriptEntry[],
  contentWidth: number,
  rowBudget: number,
  scrollOffset: number,
): {visibleEntries: TranscriptEntry[]; hiddenAbove: number; hiddenBelow: number} {
  if (!entries.length) {
    return {visibleEntries: [], hiddenAbove: 0, hiddenBelow: 0};
  }

  const safeOffset = Math.max(0, Math.min(scrollOffset, Math.max(0, entries.length - 1)));
  const endIndex = Math.max(0, entries.length - 1 - safeOffset);
  const visibleEntries: TranscriptEntry[] = [];
  let usedRows = 0;
  let afterEntry: TranscriptEntry | undefined;

  for (let index = endIndex; index >= 0; index -= 1) {
    const entry = entries[index];
    const nextHeight = estimateEntryHeight(entry, contentWidth) + (afterEntry ? gapBeforeEntry(afterEntry, entry) : 0);
    if (visibleEntries.length > 0 && usedRows + nextHeight > rowBudget) {
      break;
    }
    visibleEntries.unshift(entry);
    usedRows += nextHeight;
    afterEntry = entry;
  }

  let lastVisibleIndex = visibleEntries.length ? entries.indexOf(visibleEntries[visibleEntries.length - 1]) : endIndex;

  for (let index = lastVisibleIndex + 1; index < entries.length; index += 1) {
    const previousEntry = visibleEntries[visibleEntries.length - 1];
    const entry = entries[index];
    const nextHeight = estimateEntryHeight(entry, contentWidth) + gapBeforeEntry(entry, previousEntry);
    if (visibleEntries.length > 0 && usedRows + nextHeight > rowBudget) {
      break;
    }
    visibleEntries.push(entry);
    usedRows += nextHeight;
    lastVisibleIndex = index;
  }

  const firstVisibleIndex = visibleEntries.length ? entries.indexOf(visibleEntries[0]) : 0;
  const hiddenAbove = Math.max(0, firstVisibleIndex);
  const hiddenBelow = Math.max(0, entries.length - lastVisibleIndex - 1);

  return {
    visibleEntries,
    hiddenAbove,
    hiddenBelow,
  };
}

function useTerminalDimensions(): {columns: number; rows: number} {
  const {stdout} = useStdout();
  const [dimensions, setDimensions] = useState({
    columns: stdout.columns ?? 120,
    rows: stdout.rows ?? 40,
  });

  useEffect(() => {
    const update = () => {
      setDimensions({
        columns: stdout.columns ?? 120,
        rows: stdout.rows ?? 40,
      });
    };

    update();
    stdout.on('resize', update);

    return () => {
      stdout.off('resize', update);
    };
  }, [stdout]);

  return dimensions;
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
        dotColor: PALETTE.assistantDot,
        guideColor: '#b8b0a8',
        labelColor: PALETTE.assistantLabel,
        textColor: PALETTE.assistantText,
      };

    case 'user':
      return {
        dotColor: PALETTE.userDot,
        guideColor: '#6ea884',
        labelColor: PALETTE.userLabel,
        textColor: PALETTE.userText,
      };

    case 'reasoning':
      return {
        dotColor: PALETTE.reasoningDot,
        guideColor: '#9386ab',
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
        dotColor: color,
        guideColor: color,
        labelColor: color,
        textColor: PALETTE.toolText,
      };
    }

    case 'error':
      return {
        dotColor: PALETTE.errorText,
        guideColor: PALETTE.errorText,
        labelColor: PALETTE.errorText,
        textColor: PALETTE.errorText,
      };

    default:
      return {
        dotColor: PALETTE.metaText,
        guideColor: PALETTE.metaText,
        labelColor: PALETTE.metaText,
        textColor: PALETTE.metaText,
      };
  }
}

function TranscriptEntryView({
  entry,
  isSelected,
  contentWidth,
  gapTop,
}: {
  entry: TranscriptEntry;
  isSelected: boolean;
  contentWidth: number;
  gapTop: number;
}) {
  const palette = entryPalette(entry);
  const preview = plainEntryText(entry);
  const foldLabel =
    entry.status === 'running' ? 'live' : entry.collapsible ? (entry.collapsed ? 'folded' : 'open') : '';

  return (
    <Box flexDirection="column" marginTop={gapTop}>
      <Box>
        <Text color={isSelected ? PALETTE.focus : palette.dotColor}>●</Text>
        <Text color={palette.labelColor}>{` ${entry.label}`}</Text>
        {foldLabel ? (
          <Text color={PALETTE.metaText}>{` ${foldLabel}`}</Text>
        ) : null}
      </Box>

      <Text> </Text>

      <Box marginLeft={2}>
        <Text color={isSelected ? PALETTE.focus : palette.guideColor}>╰─</Text>
        <Box marginLeft={1} flexDirection="column" width={contentWidth} minWidth={20}>
        {entry.collapsible && entry.collapsed ? (
          <Text color={palette.textColor} wrap="truncate-end">
            {preview || '...'}
          </Text>
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
    </Box>
  );
}

function HistoryDrawer({
  conversations,
  currentConversationId,
  selectedIndex,
  drawerWidth,
  drawerHeight,
  maxVisible,
}: {
  conversations: ConversationSummary[];
  currentConversationId: string | null;
  selectedIndex: number;
  drawerWidth: number;
  drawerHeight: number;
  maxVisible: number;
}) {
  const start = Math.max(0, Math.min(selectedIndex - 4, Math.max(0, conversations.length - maxVisible)));
  const visible = conversations.slice(start, start + maxVisible);

  return (
    <Box
      width={drawerWidth}
      height={drawerHeight}
      minHeight={drawerHeight}
      marginLeft={1}
      borderStyle="round"
      borderColor={PALETTE.drawerBorder}
      paddingX={1}
      flexDirection="column"
      overflow="hidden"
    >
      <Text color={PALETTE.title}>Recent Conversations</Text>
      <Text color={PALETTE.metaText}>Up/Down move · Enter load · Del remove · Ctrl+N new</Text>

      <Box flexDirection="column" marginTop={1}>
        {visible.map((conversation, offset) => {
          const absoluteIndex = start + offset;
          const selected = absoluteIndex === selectedIndex;
          const current = conversation.id === currentConversationId;
          const prefix = selected ? '›' : ' ';
          const currentDot = current ? '●' : '○';
          const line = `${prefix} ${currentDot} ${formatConversationStamp(conversation.updated_at)} ${truncateText(conversation.title, 22)}`;

          return (
            <Text
              key={conversation.id}
              color={selected ? PALETTE.focus : current ? PALETTE.assistantText : PALETTE.userText}
              wrap="truncate-end"
            >
              {line}
            </Text>
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
  const {columns, rows} = useTerminalDimensions();
  const [status, setStatus] = useState<AppStatus | null>(null);
  const [statusError, setStatusError] = useState<string>('');
  const [conversations, setConversations] = useState<ConversationSummary[]>([]);
  const [historyOpen, setHistoryOpen] = useState(false);
  const [historySelection, setHistorySelection] = useState(0);
  const [conversationId, setConversationId] = useState<string | null>(null);
  const [selectedEntryId, setSelectedEntryId] = useState<string | null>(null);
  const [entries, setEntries] = useState<TranscriptEntry[]>(buildBlankEntries(false));
  const [input, setInput] = useState('');
  const [cursorPosition, setCursorPosition] = useState(0);
  const [busy, setBusy] = useState(false);
  const [historyBusy, setHistoryBusy] = useState(false);
  const [transcriptScrollOffset, setTranscriptScrollOffset] = useState(0);
  const deltaBufferRef = useRef<Record<string, BufferedDelta>>({});
  const deltaTimerRef = useRef<NodeJS.Timeout | null>(null);
  const pendingPromptRef = useRef<string>('');

  const flushDeltaBuffer = () => {
    const buffered = Object.values(deltaBufferRef.current);
    if (!buffered.length) {
      return;
    }

    deltaBufferRef.current = {};
    setEntries((current) => {
      let next = stripPlaceholder(current);

      for (const item of buffered) {
        next = appendOrUpdate(
          next,
          item.id,
          {
            id: item.id,
            kind: item.kind === 'assistant' ? 'assistant' : 'reasoning',
            label: item.kind === 'assistant' ? 'assistant' : 'thinking',
            text: '',
            status: item.kind === 'reasoning' ? 'running' : undefined,
            collapsible: item.kind === 'reasoning',
            collapsed: false,
          },
          (entry) => ({...entry, text: `${entry.text}${item.text}`}),
        );
      }

      return next;
    });
  };

  const scheduleDeltaFlush = () => {
    if (deltaTimerRef.current) {
      return;
    }

    deltaTimerRef.current = setTimeout(() => {
      deltaTimerRef.current = null;
      flushDeltaBuffer();
    }, DELTA_FLUSH_MS);
  };

  const queueDelta = (kind: 'assistant' | 'reasoning', blockId: string, text: string) => {
    const current = deltaBufferRef.current[blockId];
    deltaBufferRef.current[blockId] = current
      ? {...current, text: `${current.text}${text}`}
      : {id: blockId, kind, text};
    scheduleDeltaFlush();
  };

  useEffect(() => {
    return () => {
      if (deltaTimerRef.current) {
        clearTimeout(deltaTimerRef.current);
      }
    };
  }, []);

  const refreshConversations = async (preferredId?: string | null): Promise<ConversationSummary[]> => {
    const nextConversations = (await fetchConversations()).map(normalizeConversation);
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
    flushDeltaBuffer();
    const [messages, nextConversations] = await Promise.all([
      fetchMessages(targetId),
      knownConversations ? Promise.resolve(knownConversations) : refreshConversations(targetId),
    ]);

    setConversationId(targetId);
    setEntries(buildEntriesFromMessages(messages));
    setTranscriptScrollOffset(0);
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
    setTranscriptScrollOffset(0);
    setSelectedEntryId(null);
    setHistoryOpen(false);
    setInput('');
    setCursorPosition(0);
  };

  const removeConversationById = async (targetId: string) => {
    setHistoryBusy(true);
    try {
      await deleteConversation(targetId);
      const nextConversations = await refreshConversations();

      if (!nextConversations.length) {
        resetConversation();
        return;
      }

      const selectedConversation = nextConversations[Math.min(historySelection, nextConversations.length - 1)];
      if (conversationId === targetId) {
        await openConversation(selectedConversation.id, nextConversations);
        return;
      }

      setHistorySelection(Math.min(historySelection, nextConversations.length - 1));
    } finally {
      setHistoryBusy(false);
    }
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

  const handlePromptSubmit = () => {
    void submitPrompt();
  };

  const handlePromptInput = (inputValue: string, key: {backspace: boolean; delete: boolean; return: boolean; ctrl: boolean; meta: boolean; tab: boolean; escape: boolean; upArrow: boolean; downArrow: boolean; leftArrow: boolean; rightArrow: boolean;}) => {
    if (busy || historyOpen) {
      return;
    }

    if (key.return) {
      handlePromptSubmit();
      return;
    }

    if (key.leftArrow) {
      setCursorPosition((p) => Math.max(0, p - 1));
      return;
    }

    if (key.rightArrow) {
      setCursorPosition((p) => Math.min(input.length, p + 1));
      return;
    }

    if (key.backspace || key.delete) {
      if (cursorPosition > 0) {
        setInput(input.slice(0, cursorPosition - 1) + input.slice(cursorPosition));
        setCursorPosition((p) => p - 1);
      }
      return;
    }

    if (
      key.ctrl ||
      key.meta ||
      key.tab ||
      key.escape ||
      key.upArrow ||
      key.downArrow
    ) {
      return;
    }

    if (inputValue) {
      setInput(input.slice(0, cursorPosition) + inputValue + input.slice(cursorPosition));
      setCursorPosition((p) => p + inputValue.length);
    }
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

    if (key.ctrl && inputValue.toLowerCase() === 'o' && !busy && !historyBusy) {
      if (conversations.length) {
        setHistoryOpen((current) => !current);
      }
      return;
    }

    if (key.ctrl && inputValue.toLowerCase() === 'n' && !busy && !historyBusy) {
      resetConversation();
      return;
    }

    if (historyOpen && key.return && !busy && !historyBusy) {
      const targetConversation = conversations[historySelection];
      if (targetConversation) {
        void openConversation(targetConversation.id, conversations);
      }
      return;
    }

    if (historyOpen && (key.delete || key.backspace) && !busy && !historyBusy) {
      const targetConversation = conversations[historySelection];
      if (targetConversation) {
        void removeConversationById(targetConversation.id);
      }
      return;
    }

    if (!historyOpen && key.tab) {
      const nextId = nextCollapsibleId(selectedEntryId, entries);
      if (nextId) {
        setSelectedEntryId(nextId);
      }
      return;
    }

    if (!historyOpen && key.upArrow) {
      setTranscriptScrollOffset((current) => Math.min(current + 1, Math.max(0, entries.length - 1)));
      return;
    }

    if (!historyOpen && key.downArrow) {
      setTranscriptScrollOffset((current) => Math.max(0, current - 1));
      return;
    }

    if (!historyOpen && key.pageUp) {
      setTranscriptScrollOffset((current) => Math.min(current + 5, Math.max(0, entries.length - 1)));
      return;
    }

    if (!historyOpen && key.pageDown) {
      setTranscriptScrollOffset((current) => Math.max(0, current - 5));
      return;
    }

    if (!historyOpen && key.ctrl && inputValue.toLowerCase() === 'e') {
      toggleSelectedEntry();
      return;
    }

    if (historyOpen && key.upArrow) {
      setHistorySelection((current) => Math.max(0, current - 1));
      return;
    }

    if (historyOpen && key.downArrow) {
      setHistorySelection((current) => Math.min(conversations.length - 1, current + 1));
      return;
    }

    handlePromptInput(inputValue, key);
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

  useEffect(() => {
    setTranscriptScrollOffset((current) => Math.min(current, Math.max(0, entries.length - 1)));
  }, [entries.length, columns, rows]);

  useEffect(() => {
    setHistorySelection((current) => Math.min(current, Math.max(0, conversations.length - 1)));
  }, [conversations.length, columns, rows]);

  const applyEvent = (event: UIStreamEvent) => {
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
        setTranscriptScrollOffset(0);
        setEntries((current) => [
          ...stripPlaceholder(current),
          {
            id: event.message_id ?? `user-${Date.now()}`,
            kind: 'user',
            label: 'you',
            text: eventText(event, 'content') || pendingPromptRef.current,
          },
        ]);
        pendingPromptRef.current = '';
        break;

      case 'assistant.delta': {
        const blockId = event.block_id ?? `assistant-${Date.now()}`;
        const delta = eventText(event, 'text');
        queueDelta('assistant', blockId, delta);
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
        queueDelta('reasoning', blockId, delta);
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

      case 'conversation.updated': {
        const summary = readConversationSummary(event);
        if (summary) {
          setConversations((current) => upsertConversation(current, summary));
        }
        break;
      }

      case 'assistant.completed':
      case 'session.completed':
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
    setCursorPosition(0);
    pendingPromptRef.current = prompt;
    setTranscriptScrollOffset(0);
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
  const historyDrawerMaxWidth = Math.max(
    HISTORY_DRAWER_MIN_WIDTH,
    columns - TRANSCRIPT_MIN_WIDTH - 8,
  );
  const historyDrawerWidth = historyOpen
    ? Math.max(
        HISTORY_DRAWER_MIN_WIDTH,
        Math.min(HISTORY_DRAWER_WIDTH, Math.floor(columns * 0.32), historyDrawerMaxWidth),
      )
    : 0;
  const transcriptWidth = historyOpen
    ? Math.max(TRANSCRIPT_MIN_WIDTH, columns - historyDrawerWidth - 8)
    : Math.max(TRANSCRIPT_MIN_WIDTH, columns - 6);
  const transcriptPanelHeight = Math.max(8, rows - 16);
  const transcriptRowBudget = Math.max(6, transcriptPanelHeight - 4);
  const historyMaxVisible = Math.max(4, transcriptPanelHeight - 5);
  const {visibleEntries, hiddenAbove, hiddenBelow} = buildTranscriptViewport(
    entries,
    Math.max(32, transcriptWidth - 4),
    transcriptRowBudget,
    transcriptScrollOffset,
  );
  const footerText = historyOpen
    ? 'Enter load · Up/Down move · Del remove · Ctrl+N new · Esc close'
    : 'Enter submit · Ctrl+O history · Ctrl+N new · Up/Down scroll · Tab select fold · Ctrl+E toggle · Esc exit';

  return (
    <Box flexDirection="column" padding={1} height={rows} overflow="hidden">
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

      <Box marginTop={1} height={transcriptPanelHeight} minHeight={transcriptPanelHeight} overflow="hidden">
        <Box
          flexDirection="column"
          flexGrow={1}
          width={transcriptWidth}
          height={transcriptPanelHeight}
          minHeight={transcriptPanelHeight}
          borderStyle="round"
          borderColor={historyOpen ? PALETTE.headerIdle : 'gray'}
          paddingX={1}
          minWidth={0}
          overflow="hidden"
        >
          {hiddenAbove > 0 ? (
            <Text color={PALETTE.metaText}>{`${hiddenAbove} earlier blocks above · Up to review`}</Text>
          ) : null}

          {visibleEntries.map((entry, index) => (
            <TranscriptEntryView
              key={entry.id}
              entry={entry}
              isSelected={entry.id === selectedEntryId}
              contentWidth={Math.max(28, transcriptWidth - 6)}
              gapTop={gapBeforeEntry(entry, visibleEntries[index - 1])}
            />
          ))}

          {hiddenBelow > 0 ? (
            <Text color={PALETTE.metaText}>{`${hiddenBelow} newer blocks below · Down to follow latest`}</Text>
          ) : null}
        </Box>

        {historyOpen ? (
          <HistoryDrawer
            conversations={conversations}
            currentConversationId={conversationId}
            selectedIndex={historySelection}
            drawerWidth={historyDrawerWidth}
            drawerHeight={transcriptPanelHeight}
            maxVisible={historyMaxVisible}
          />
        ) : null}
      </Box>

      <Box
        marginTop={1}
        borderStyle="round"
        borderColor={busy ? PALETTE.headerBusy : PALETTE.headerIdle}
        paddingX={1}
        minHeight={3}
      >
        <Text color={PALETTE.metaText}>prompt  </Text>
        {input ? (
          <>
            <Text color={PALETTE.assistantText}>{input.slice(0, cursorPosition)}</Text>
            {!historyOpen ? <Text color={PALETTE.focus}>█</Text> : null}
            <Text color={PALETTE.assistantText}>{input.slice(cursorPosition)}</Text>
          </>
        ) : (
          <>
            {!historyOpen ? <Text color={PALETTE.focus}>█</Text> : null}
            <Text color={PALETTE.metaText}>{'type a message...'}</Text>
          </>
        )}
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