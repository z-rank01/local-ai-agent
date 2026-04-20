import React, {useEffect, useState} from 'react';
import {Box, Text, useApp, useInput} from 'ink';
import TextInput from 'ink-text-input';

import {fetchStatus, streamChat} from './api-client.js';
import type {AppStatus, UIStreamEvent} from './types.js';

type TranscriptEntry = {
  id: string;
  kind: 'meta' | 'user' | 'assistant' | 'reasoning' | 'tool' | 'error';
  label: string;
  text: string;
  status?: 'running' | 'ok' | 'error';
};

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

export function ShellApp() {
  const {exit} = useApp();
  const [status, setStatus] = useState<AppStatus | null>(null);
  const [statusError, setStatusError] = useState<string>('');
  const [conversationId, setConversationId] = useState<string | null>(null);
  const [entries, setEntries] = useState<TranscriptEntry[]>([
    {
      id: 'boot',
      kind: 'meta',
      label: 'system',
      text: 'Ink shell bootstrap ready. Waiting for adapter status...',
    },
  ]);
  const [input, setInput] = useState('');
  const [busy, setBusy] = useState(false);

  useInput((_input, key) => {
    if (key.escape) {
      exit();
    }
  });

  useEffect(() => {
    void (async () => {
      try {
        const nextStatus = await fetchStatus();
        setStatus(nextStatus);
        setEntries((current) => [
          ...current,
          {
            id: 'status-ready',
            kind: 'meta',
            label: 'adapter',
            text: `connected · model ${nextStatus.model} · ${nextStatus.tools.length} tools`,
          },
        ]);
      } catch (error) {
        setStatusError(error instanceof Error ? error.message : String(error));
      }
    })();
  }, []);

  const applyEvent = (event: UIStreamEvent) => {
    if (event.conversation_id) {
      setConversationId(event.conversation_id);
    }

    switch (event.event) {
      case 'session.started':
        setEntries((current) => [
          ...current,
          {
            id: `session-${event.run_id}`,
            kind: 'meta',
            label: 'session',
            text: `conversation ${event.conversation_id ?? 'unknown'} started`,
          },
        ]);
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
          ...current,
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
          ...current,
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
            current,
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
        setEntries((current) => [
          ...current,
          {
            id: blockId,
            kind: 'reasoning',
            label: 'thinking',
            text: '',
            status: 'running',
          },
        ]);
        break;
      }

      case 'reasoning.delta': {
        const blockId = event.block_id ?? `reasoning-${Date.now()}`;
        const delta = eventText(event, 'text');
        setEntries((current) =>
          appendOrUpdate(
            current,
            blockId,
            {
              id: blockId,
              kind: 'reasoning',
              label: 'thinking',
              text: '',
              status: 'running',
            },
            (entry) => ({...entry, text: `${entry.text}${delta}`}),
          ),
        );
        break;
      }

      case 'reasoning.completed':
        if (event.block_id) {
          setEntries((current) =>
            appendOrUpdate(
              current,
              event.block_id,
              {
                id: event.block_id,
                kind: 'reasoning',
                label: 'thinking',
                text: '',
                status: 'ok',
              },
              (entry) => ({...entry, status: 'ok'}),
            ),
          );
        }
        break;

      case 'tool.started': {
        const blockId = event.block_id ?? `tool-${Date.now()}`;
        const label = typeof event.data.name === 'string' ? event.data.name : 'tool';
        setEntries((current) => [
          ...current,
          {
            id: blockId,
            kind: 'tool',
            label,
            text: eventText(event, 'summary'),
            status: 'running',
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
            },
            (entry) => ({...entry, text: detail || entry.text, status: statusValue}),
          ),
        );
        break;
      }

      case 'error':
        setEntries((current) => [
          ...current,
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
        ...current,
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

  return (
    <Box flexDirection="column" padding={1}>
      <Box borderStyle="round" borderColor="cyan" paddingX={1} paddingY={0} flexDirection="column">
        <Box justifyContent="space-between">
          <Text color="cyanBright">Local AI Agent / Ink shell</Text>
          <Text color={busy ? 'yellow' : 'gray'}>{busy ? 'streaming' : 'idle'}</Text>
        </Box>
        <Box marginTop={1}>
          <Text color="gray">
            {statusError
              ? `adapter offline · ${statusError}`
              : status
                ? `model ${status.model} · workspace ${status.workspace_path}`
                : 'loading adapter status...'}
          </Text>
        </Box>
      </Box>

      <Box flexDirection="column" marginTop={1} borderStyle="round" borderColor="gray" paddingX={1}>
        {entries.map((entry) => {
          const color =
            entry.kind === 'assistant'
              ? 'white'
              : entry.kind === 'user'
                ? 'cyan'
                : entry.kind === 'reasoning'
                  ? 'yellow'
                  : entry.kind === 'tool'
                    ? entry.status === 'error'
                      ? 'red'
                      : entry.status === 'ok'
                        ? 'green'
                        : 'gray'
                    : entry.kind === 'error'
                      ? 'redBright'
                      : 'gray';
          const marker =
            entry.kind === 'assistant'
              ? '»'
              : entry.kind === 'user'
                ? '›'
                : entry.kind === 'reasoning'
                  ? '·'
                  : entry.kind === 'tool'
                    ? '•'
                    : '·';
          return (
            <Box key={entry.id} marginBottom={0}>
              <Text color={color}>{`${marker} ${entry.label}`}</Text>
              <Text color="gray">  </Text>
              <Text color={color}>{entry.text || (entry.status === 'running' ? '...' : '')}</Text>
            </Box>
          );
        })}
      </Box>

      <Box marginTop={1} borderStyle="round" borderColor={busy ? 'yellow' : 'cyan'} paddingX={1}>
        <Text color="gray">prompt  </Text>
        <TextInput value={input} onChange={setInput} onSubmit={submitPrompt} />
      </Box>

      <Box marginTop={1}>
        <Text color="gray">Enter submit · Esc exit · current conversation {conversationId ?? 'none'}</Text>
      </Box>
    </Box>
  );
}