import type {ComponentType} from 'react';
import type {ConversationSummary, TranscriptBlock} from '../types';
import type {ReadingReport} from '../lib/report';

/** Public settings contribution: no access to App's private state. */
export type CapabilityPanelProps = {
  conversation?: ConversationSummary;
  onChanged?: () => void;
  onMethodChanged?: (conversation: ConversationSummary) => void;
};
export type UIContribution = {
  id: string;
  title: string;
  description: string;
  load: () => Promise<{default: ComponentType<CapabilityPanelProps>}>;
};
export type ArtifactInfo = ReadingReport & {meta: string[]};
export type SavedArtifact = {
  conversation_id: string;
  identity: string;
  revision: string;
  content: string;
  visibility: string;
  completeness: string;
  media_type: string;
  artifact_type: string;
  owner: string;
};
export type ArtifactContribution = {
  type: string;
  tools: string[];
  load: () => Promise<{getArtifactInfo: (block: TranscriptBlock, content?: string, conversationId?: string) => ArtifactInfo | Promise<ArtifactInfo>}>;
};
