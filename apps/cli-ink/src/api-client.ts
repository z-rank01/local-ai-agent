import type {AppStatus, ChatRequest, UIStreamEvent} from './types.js';

const DEFAULT_BASE_URL = process.env.LOCAL_AI_AGENT_API_URL ?? 'http://127.0.0.1:9510';

export async function fetchStatus(baseUrl = DEFAULT_BASE_URL): Promise<AppStatus> {
  const response = await fetch(`${baseUrl}/api/status`);
  if (!response.ok) {
    throw new Error(`status request failed: ${response.status}`);
  }
  return (await response.json()) as AppStatus;
}

export async function streamChat(
  request: ChatRequest,
  onEvent: (event: UIStreamEvent) => void,
  baseUrl = DEFAULT_BASE_URL,
): Promise<void> {
  const response = await fetch(`${baseUrl}/api/chat/stream`, {
    method: 'POST',
    headers: {
      'content-type': 'application/json',
    },
    body: JSON.stringify(request),
  });

  if (!response.ok || !response.body) {
    throw new Error(`chat stream failed: ${response.status}`);
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = '';

  while (true) {
    const {done, value} = await reader.read();
    if (done) {
      break;
    }
    buffer += decoder.decode(value, {stream: true});
    let newlineIndex = buffer.indexOf('\n');
    while (newlineIndex >= 0) {
      const line = buffer.slice(0, newlineIndex).trim();
      buffer = buffer.slice(newlineIndex + 1);
      if (line) {
        onEvent(JSON.parse(line) as UIStreamEvent);
      }
      newlineIndex = buffer.indexOf('\n');
    }
  }

  if (buffer.trim()) {
    onEvent(JSON.parse(buffer) as UIStreamEvent);
  }
}