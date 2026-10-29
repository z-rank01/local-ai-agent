// Single source of truth for persisted localStorage keys plus never-throwing
// read/write helpers (storage can be unavailable or full).

export const STORAGE_KEYS = {
  appearance: 'local-ai-agent.appearance.v1',
  activeConversation: 'local-ai-agent.activeConversation.v1',
  capabilityRecent: 'local-ai-agent.capability-recent.v1',
  panel: (id: string) => `local-ai-agent.panel.${id}`,
  sheetWidth: 'local-ai-agent.sheet-width',
} as const;

export function readJson<T>(key: string, fallback: T): T {
  try {
    const raw = window.localStorage.getItem(key);
    return raw === null ? fallback : (JSON.parse(raw) as T);
  } catch {
    return fallback;
  }
}

export function writeJson(key: string, value: unknown): void {
  try {
    window.localStorage.setItem(key, JSON.stringify(value));
  } catch {
    // Persistence is best-effort.
  }
}

export function readString(key: string, fallback: string | null = null): string | null {
  try {
    return window.localStorage.getItem(key) ?? fallback;
  } catch {
    return fallback;
  }
}

export function writeString(key: string, value: string): void {
  try {
    window.localStorage.setItem(key, value);
  } catch {
    // Persistence is best-effort.
  }
}
