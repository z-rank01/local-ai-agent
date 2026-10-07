import {clamp} from './format';
import {readJson, STORAGE_KEYS} from './storage';

export type DensityMode = 'comfortable' | 'compact' | 'spacious';

export type AppearanceSettings = {
  uiFont: string;
  codeFont: string;
  contentFontSize: number;
  codeFontSize: number;
  readingWidth: number;
  lineHeight: number;
  density: DensityMode;
};

export const DEFAULT_UI_FONT = 'Inter, "Segoe UI", "Microsoft YaHei UI", "PingFang SC", "Noto Sans CJK SC", ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, sans-serif';
export const DEFAULT_CODE_FONT = '"Cascadia Code", "JetBrains Mono", "Sarasa Mono SC", "SFMono-Regular", Consolas, "Liberation Mono", monospace';

export const DEFAULT_APPEARANCE: AppearanceSettings = {
  uiFont: DEFAULT_UI_FONT,
  codeFont: DEFAULT_CODE_FONT,
  contentFontSize: 15,
  codeFontSize: 13,
  readingWidth: 880,
  lineHeight: 1.75,
  density: 'comfortable',
};

export const UI_FONT_PRESETS = [
  {label: '系统默认', value: DEFAULT_UI_FONT},
  {label: 'Segoe UI / 微软雅黑', value: '"Segoe UI", "Microsoft YaHei UI", "Microsoft YaHei", system-ui, sans-serif'},
  {label: 'PingFang / Noto Sans', value: '"PingFang SC", "Noto Sans CJK SC", "Microsoft YaHei UI", system-ui, sans-serif'},
  {label: 'Inter', value: 'Inter, "Segoe UI", "Microsoft YaHei UI", system-ui, sans-serif'},
];

export const CODE_FONT_PRESETS = [
  {label: '系统默认', value: DEFAULT_CODE_FONT},
  {label: 'Cascadia Code', value: '"Cascadia Code", "Sarasa Mono SC", Consolas, monospace'},
  {label: 'JetBrains Mono', value: '"JetBrains Mono", "Sarasa Mono SC", Consolas, monospace'},
  {label: 'Consolas', value: 'Consolas, "Courier New", monospace'},
];

export const DENSITY_OPTIONS: Array<{label: string; value: DensityMode; scale: number}> = [
  {label: '紧凑', value: 'compact', scale: 0.86},
  {label: '标准', value: 'comfortable', scale: 1},
  {label: '宽松', value: 'spacious', scale: 1.14},
];

export function normalizeAppearanceSettings(value: Partial<AppearanceSettings> | null | undefined): AppearanceSettings {
  return {
    uiFont: typeof value?.uiFont === 'string' && value.uiFont.trim() ? value.uiFont.trim() : DEFAULT_APPEARANCE.uiFont,
    codeFont: typeof value?.codeFont === 'string' && value.codeFont.trim() ? value.codeFont.trim() : DEFAULT_APPEARANCE.codeFont,
    contentFontSize: clamp(Number(value?.contentFontSize) || DEFAULT_APPEARANCE.contentFontSize, 13, 19),
    codeFontSize: clamp(Number(value?.codeFontSize) || DEFAULT_APPEARANCE.codeFontSize, 12, 17),
    readingWidth: clamp(Number(value?.readingWidth) || DEFAULT_APPEARANCE.readingWidth, 420, 1120),
    lineHeight: clamp(Number(value?.lineHeight) || DEFAULT_APPEARANCE.lineHeight, 1.45, 2.05),
    density: value?.density === 'compact' || value?.density === 'spacious' ? value.density : DEFAULT_APPEARANCE.density,
  };
}

export function loadAppearanceSettings(): AppearanceSettings {
  return normalizeAppearanceSettings(readJson<Partial<AppearanceSettings> | null>(STORAGE_KEYS.appearance, null));
}

export function densityScale(density: DensityMode): number {
  return DENSITY_OPTIONS.find((item) => item.value === density)?.scale ?? 1;
}
