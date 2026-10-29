import type {ConversationSummary, TranscriptBlock, UIStreamEvent} from '../types';
import {artifactTypeForTool} from '../extensions/artifacts';
import type {ReportPage, ReportSection} from './reportDocument';

export type ReadingReport = {
  htmlBrief?: import('./htmlBrief').HtmlBrief;
  title: string;
  content: string;
  sourceMessageId?: string;
  page?: number;
  pageCount?: number;
  section?: ReportSection;
  readerDocument?: ReportPage;
  readerPositions?: Partial<Record<ReportSection, ReportPage>>;
  sections?: ReportSection[];
  sectionLabels?: Partial<Record<ReportSection, string>>;
};

export function localReportContent(block: TranscriptBlock): string {
  const toolResult = block.toolResult as {local_result?: unknown} | null | undefined;
  return typeof toolResult?.local_result === 'string' ? toolResult.local_result : '';
}

export function isLocalReportBlock(block: TranscriptBlock): boolean {
  if (block.label === 'brief_publish') {
    const result = block.toolResult as {status?: unknown; path?: unknown} | null | undefined;
    return result?.status === 'PUBLISHED' && typeof result.path === 'string';
  }
  const result = block.toolResult as {local_result_available?: unknown} | null | undefined;
  return Boolean(artifactTypeForTool(block.label) && block.messageId
    && (localReportContent(block) || result?.local_result_available === true));
}

export function buildGenericArtifactInfo(block: TranscriptBlock): ReadingReport & {meta: string[]} {
  return {title: '本地报告', content: localReportContent(block), sourceMessageId: block.messageId ?? undefined,
    page: typeof block.params?.page === 'number' ? block.params.page : 1, meta: []};
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
        capability_ids: Array.isArray(item.capability_ids)
          ? item.capability_ids.filter((id): id is string => typeof id === 'string')
          : [],
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
