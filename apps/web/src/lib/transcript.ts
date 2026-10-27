import type {MessageRecord, TranscriptBlock} from '../types';
import {splitStoredToolContent} from './format';
import {isLocalReportBlock} from './report';

export type AssistantTranscriptGroup = {
  id: string;
  kind: 'assistant-group';
  blocks: TranscriptBlock[];
};

export type AssistantTimelineEntry = {
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

export type TranscriptViewItem = TranscriptBlock | AssistantTranscriptGroup;

export type EditingState = {
  messageId: string;
  originalText: string;
};

export function appendOrUpdate(
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

export function insertBeforeBlock(
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

export function isAssistantSideBlock(block: TranscriptBlock): boolean {
  return block.kind === 'assistant' || block.kind === 'reasoning' || block.kind === 'tool';
}

export function buildTranscriptViewItems(blocks: TranscriptBlock[]): TranscriptViewItem[] {
  const items: TranscriptViewItem[] = [];
  let group: AssistantTranscriptGroup | null = null;
  for (const block of blocks) {
    if (block.fileDelivery) {
      group = null;
      items.push({id: `assistant-group-${block.id}`, kind: 'assistant-group', blocks: [block]});
      continue;
    }
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

export function assistantGroupStatus(blocks: TranscriptBlock[]): TranscriptBlock['status'] | undefined {
  if (blocks.some((block) => block.status === 'running' || block.placeholder)) {
    return 'running';
  }
  // A completed answer can describe a partial result or a recovered tool error.
  // Keep tool failures in the trace; they are not the final answer's status.
  const last = blocks[blocks.length - 1];
  if (last?.kind === 'assistant' && last.status === 'ok') {
    return undefined;
  }
  if (blocks.some((block) => block.status === 'error')) {
    return 'error';
  }
  return undefined;
}

export function buildAssistantTimeline(
  blocks: TranscriptBlock[],
  groupStatus: TranscriptBlock['status'] | undefined,
  versionNumber: number,
  versionCount: number,
): AssistantTimelineEntry[] {
  const toolAttempts = new Map<string, number>();
  return blocks.filter(block => block.kind !== 'assistant' || block.text || block.status === 'running').map((block, index) => {
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
    const hasLocalResult = typeof (block.toolResult as {local_result?: unknown} | null | undefined)?.local_result === 'string';
    const isLocalReport = isLocalReportBlock(block);
    const badges: string[] = [];
    if (attempt > 1) {
      badges.push(`第 ${attempt} 次调用`);
    }
    if ((block.status ?? groupStatus) === 'error') {
      badges.push('失败节点');
    }
    if (hasLocalResult) {
      badges.push(isLocalReport ? '研究材料' : '本地结果');
    }
    return {
      id: block.id,
      stepNumber,
      kind: 'tool',
      title: `调用 ${block.label}`,
      subtitle: isLocalReport
        ? '内部研究材料已保存'
        : block.summary || (block.toolResult != null ? '结构化结果已保存' : block.text ? '已返回结果' : '等待结果'),
      status: block.status ?? groupStatus,
      elapsed: block.elapsed,
      collapsible: Boolean(block.collapsible),
      collapsed: Boolean(block.collapsed),
      badges,
    } satisfies AssistantTimelineEntry;
  });
}

export function buildBlocksFromMessages(messages: MessageRecord[]): TranscriptBlock[] {
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
      if (message.content) blocks.push({
        id: message.id,
        kind: 'assistant',
        label: message.model || '助手',
        fileDelivery: message.file_delivery,
        text: message.content,
        messageId: message.id,
        createdAt: message.created_at,
        status: message.status === 'error' || message.status === 'interrupted' ? 'error' : 'ok',
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
        params: message.params,
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
