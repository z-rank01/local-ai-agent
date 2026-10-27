import type {TranscriptBlock} from '../../types';
import type {ArtifactInfo} from '../../extensions/contracts';
import {formatTime} from '../../lib/format';
import {fetchConversation, fetchWorkspacePreview} from '../../api';
import {prepareHtmlBrief} from '../../lib/htmlBrief';

export async function getArtifactInfo(block: TranscriptBlock, savedContent?: string, conversationId?: string): Promise<ArtifactInfo> {
  const toolResult = (block.toolResult && typeof block.toolResult === 'object'
    ? block.toolResult
    : {}) as Record<string, unknown>;
  if (block.label === 'brief_publish' && typeof toolResult.path === 'string' && conversationId) {
    const conversation = await fetchConversation(conversationId);
    const preview = await fetchWorkspacePreview(toolResult.path, 200000, conversation.workspace_id);
    const htmlBrief = prepareHtmlBrief(preview, conversation.workspace_id);
    return {title: htmlBrief.title, meta: htmlBrief.meta, content: preview.content ?? '',
      htmlBrief, sourceMessageId: block.messageId ?? undefined,
      sections: ['body', 'references'], sectionLabels: {body: '正文', references: '参考资料'}};
  }
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
  const result = block.toolResult as {local_result?: unknown} | null | undefined;
  const content = savedContent ?? (typeof result?.local_result === 'string' ? result.local_result : '');
  const meta: string[] = [];
  const footer = content.trim().match(/\n第 (\d+)\/(\d+) 页。可继续说[^\n]*$/u);
  const page = footer ? Number(footer[1]) : typeof block.params?.page === 'number'
    ? block.params.page
    : typeof observation?.requested_page === 'number'
      ? observation.requested_page
      : null;
  const pageCount = footer ? Number(footer[2]) : typeof observation?.page_count === 'number' ? observation.page_count : null;
  if (page !== null) {
    meta.push(pageCount ? `原整篇 第 ${page} / ${pageCount} 页` : `原整篇 第 ${page} 页`);
  }
  const version = ['version', 'report_version', 'brief_version', 'version_number']
    .map(key => observation?.[key])
    .find(value => typeof value === 'string' || typeof value === 'number');
  if (version !== undefined) meta.push(`版本 ${String(version)}`);
  if (block.createdAt) meta.push(`读取于 ${formatTime(block.createdAt)}`);
  return {title, meta, content, sourceMessageId: block.messageId ?? undefined,
    page: page ?? 1, pageCount: pageCount ?? undefined,
    sections: ['body', 'references'], sectionLabels: {body: '正文', references: '参考资料'}};
}
