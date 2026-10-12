import type {ConversationSummary, TranscriptBlock, UIStreamEvent} from '../types';
import {formatTime} from './format';

export type ReadingReport = {
  title: string;
  content: string;
  sourceMessageId?: string;
  page?: number;
  pageCount?: number;
};

export function localReportContent(block: TranscriptBlock): string {
  const toolResult = block.toolResult as {local_result?: unknown} | null | undefined;
  return typeof toolResult?.local_result === 'string' ? toolResult.local_result : '';
}

export function isLocalReportBlock(block: TranscriptBlock): boolean {
  return block.kind === 'tool' && block.label === 'stock_report_read' && Boolean(localReportContent(block));
}

export function buildReportCardInfo(block: TranscriptBlock): ReadingReport & {meta: string[]} {
  const toolResult = (block.toolResult && typeof block.toolResult === 'object'
    ? block.toolResult
    : {}) as Record<string, unknown>;
  const observation = toolResult.model_observation && typeof toolResult.model_observation === 'object'
    ? toolResult.model_observation as Record<string, unknown>
    : undefined;
  const kind = typeof observation?.kind === 'string' ? observation.kind : '';
  const baseName = kind.startsWith('market_brief') ? '市场简报' : '本地报告';
  let tradingDay = '';
  const directDay = observation?.target_date ?? observation?.trading_day;
  if (typeof directDay === 'string' && directDay) {
    tradingDay = directDay;
  } else if (typeof observation?.trading_day_description === 'string') {
    tradingDay = observation.trading_day_description.match(/\d{4}-\d{2}-\d{2}/)?.[0] ?? '';
  }
  const title = tradingDay ? `${baseName} · ${tradingDay}` : baseName;
  const meta: string[] = [];
  // The renderer's footer also fixes older saved tool results whose total was
  // mistakenly computed from the selected page rather than the whole report.
  const footer = localReportContent(block).trim().match(/\n第 (\d+)\/(\d+) 页。可继续说[^\n]*$/u);
  const page = footer ? Number(footer[1]) : typeof block.params?.page === 'number'
    ? block.params.page
    : typeof observation?.requested_page === 'number'
      ? observation.requested_page
      : null;
  const pageCount = footer ? Number(footer[2]) : typeof observation?.page_count === 'number' ? observation.page_count : null;
  if (page !== null) {
    meta.push(pageCount ? `第 ${page} / ${pageCount} 页` : `第 ${page} 页`);
  }
  const version = ['version', 'report_version', 'brief_version', 'version_number']
    .map((key) => observation?.[key])
    .find((value) => typeof value === 'string' || typeof value === 'number');
  if (version !== undefined) {
    meta.push(`版本 ${String(version)}`);
  }
  if (block.createdAt) {
    meta.push(`读取于 ${formatTime(block.createdAt)}`);
  }
  return {title, meta, content: localReportContent(block), sourceMessageId: block.messageId ?? undefined,
    page: page ?? 1, pageCount: pageCount ?? undefined};
}

export function readConversationSummary(event: UIStreamEvent): ConversationSummary | null {
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
        research_method: item.research_method ?? 'auto',
        workspace_id: item.workspace_id ?? 'default',
        workspace_name: item.workspace_name ?? '默认工作区',
      }
    : null;
}

export function upsertConversation(
  conversations: ConversationSummary[],
  next: ConversationSummary,
): ConversationSummary[] {
  const exists = conversations.some((item) => item.id === next.id);
  const merged = exists
    ? conversations.map((item) => (item.id === next.id ? next : item))
    : [next, ...conversations];
  return [...merged].sort((a, b) => b.updated_at.localeCompare(a.updated_at));
}
