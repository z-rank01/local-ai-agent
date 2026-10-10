export type ThemeMode = 'system' | 'light' | 'dark';
export type ResolvedTheme = 'light' | 'dark';

export function normalizeTheme(value: unknown): ThemeMode {
  return value === 'light' || value === 'dark' ? value : 'system';
}

export function resolveTheme(mode: ThemeMode, systemDark: boolean): ResolvedTheme {
  return mode === 'system' ? (systemDark ? 'dark' : 'light') : mode;
}

/** Apply to html so portals inherit the same theme; cleanup only the listener. */
export function watchTheme(mode: ThemeMode): () => void {
  let media: MediaQueryList | null = null;
  try {
    if (typeof window.matchMedia === 'function') media = window.matchMedia('(prefers-color-scheme: dark)');
  } catch { /* Some embedded hosts restrict media queries; use light. */ }
  const apply = () => { document.documentElement.dataset.theme = resolveTheme(mode, media?.matches ?? false); };
  apply();
  if (mode !== 'system' || !media) return () => {};
  if (typeof media.addEventListener === 'function') {
    media.addEventListener('change', apply);
    return () => media.removeEventListener('change', apply);
  }
  media.addListener(apply);
  return () => media.removeListener(apply);
}
