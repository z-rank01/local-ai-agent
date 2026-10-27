import type {TranscriptBlock} from '../types';
import type {ArtifactInfo} from './contracts';
import {artifactContributions} from './generated';

export type {ArtifactInfo} from './contracts';

export function artifactTypeForTool(tool: string): string | null {
  return artifactContributions.find(contribution => contribution.tools.includes(tool))?.type ?? null;
}

export async function getArtifactInfo(block: TranscriptBlock, conversationId?: string): Promise<ArtifactInfo> {
  const type = artifactTypeForTool(block.label);
  const contribution = artifactContributions.find(entry => entry.type === type);
  if (contribution) {
    const saved = conversationId && block.messageId && block.label !== 'brief_publish'
      ? await import('../api').then(api => api.fetchSavedArtifact(conversationId, block.messageId!))
      : null;
    return (await contribution.load()).getArtifactInfo(block, saved?.content, conversationId);
  }
  const result = block.toolResult as {local_result?: unknown} | null | undefined;
  return {
    title: '本地报告',
    content: typeof result?.local_result === 'string' ? result.local_result : '',
    sourceMessageId: block.messageId ?? undefined,
    page: typeof block.params?.page === 'number' ? block.params.page : 1,
    meta: [],
  };
}
