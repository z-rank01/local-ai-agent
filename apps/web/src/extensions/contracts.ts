import type {ComponentType} from 'react';
import type {ConversationSummary} from '../types';

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
