export type AppStatus = {
  status: string;
  model: string;
  workspace_path: string;
  tools: string[];
  websearch_enabled: boolean;
};

export type ConversationSummary = {
  id: string;
  title: string;
  model: string;
  created_at: string;
  updated_at: string;
};

export type MessageRecord = {
  id: string;
  conversation_id: string;
  role: string;
  content: string;
  thinking: string;
  tool_calls: Array<Record<string, unknown>>;
  tool_name: string;
  tool_result?: unknown;
  response_to_message_id: string;
  version_number: number;
  version_count: number;
  active: boolean;
  created_at: string;
};

export type ModelInfo = {
  id: string;
  name: string;
  provider_id: string;
  provider_name: string;
  default: boolean;
  capabilities: string[];
  context_window?: number | null;
  status: string;
};

export type ProviderInfo = {
  id: string;
  name: string;
  kind: string;
  enabled: boolean;
  base_url?: string | null;
  models: ModelInfo[];
};

export type ChatRequest = {
  message: string;
  conversation_id?: string | null;
  title?: string | null;
  provider_id?: string | null;
  model?: string | null;
  options?: Record<string, unknown>;
};

export type ImportedAttachment = {
  source_path: string;
  local_path: string;
  workspace_path: string;
  display_name: string;
};

export type WorkspaceEntry = {
  name: string;
  path: string;
  kind: 'file' | 'directory';
  size?: number | null;
  modified_at?: string | null;
  mime_type?: string | null;
};

export type WorkspaceTreeResponse = {
  root: string;
  entries: WorkspaceEntry[];
};

export type WorkspaceFilePreview = {
  name: string;
  path: string;
  size: number;
  modified_at?: string | null;
  mime_type?: string | null;
  encoding?: string | null;
  content?: string | null;
  is_binary: boolean;
  truncated: boolean;
  max_bytes: number;
};

export type WorkspaceUploadResponse = {
  attachment: ImportedAttachment;
  entry: WorkspaceEntry;
};

export type UIStreamEvent = {
  event: string;
  conversation_id?: string | null;
  run_id?: string | null;
  block_id?: string | null;
  message_id?: string | null;
  data: Record<string, unknown>;
};

export type TranscriptKind = 'user' | 'assistant' | 'reasoning' | 'tool' | 'meta' | 'error';

export type TranscriptBlock = {
  id: string;
  kind: TranscriptKind;
  label: string;
  text: string;
  summary?: string;
  messageId?: string | null;
  status?: 'running' | 'ok' | 'error';
  collapsible?: boolean;
  collapsed?: boolean;
  placeholder?: boolean;
  params?: Record<string, unknown>;
  toolResult?: unknown;
  elapsed?: number;
  createdAt?: string;
  responseToMessageId?: string;
  versionNumber?: number;
  versionCount?: number;
};
