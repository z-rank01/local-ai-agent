export type AppStatus = {
  status: string;
  model: string;
  workspace_path: string;
  tools: string[];
  websearch_enabled: boolean;
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