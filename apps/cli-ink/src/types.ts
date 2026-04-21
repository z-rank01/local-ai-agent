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
  created_at: string;
};

export type ChatRequest = {
  message: string;
  conversation_id?: string | null;
  title?: string | null;
};

export type UIStreamEvent = {
  event: string;
  conversation_id?: string | null;
  run_id?: string | null;
  block_id?: string | null;
  message_id?: string | null;
  data: Record<string, unknown>;
};