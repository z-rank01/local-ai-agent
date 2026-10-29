import type {CapabilitySummary, ManagedSkill} from '../../api';
import type {InstalledPackage} from '../../extensions/packages';
import {capabilityIcon, kindLabel, skillDisplayName} from './capabilityModel';
import {entryState, inventory, type CapabilityEntry} from './inventory';

export type ManagerKind = 'all' | 'skill' | 'plugin';
export type ManagerTone = 'ok' | 'warn' | 'danger' | 'quiet';

export type ManagerItem = {
  entry: CapabilityEntry;
  key: string;
  id: string;
  kind: 'skill' | 'plugin';
  kindLabel: string;
  name: string;
  description: string;
  icon: string;
  enabled: boolean;
  selected: boolean;
  statusLabel: string;
  tone: ManagerTone;
  source: string;
  origin: string;
  version: string;
  error: string;
  removed: boolean;
};

export type ManagerCounts = {
  all: number;
  skill: number;
  plugin: number;
  enabled: number;
  selected: number;
  removed: number;
};

export function capabilityDisplayName(entry: CapabilityEntry): string {
  return entry.kind === 'skill' ? skillDisplayName(entry.id) : entry.title || entry.id;
}

export function capabilityDisplayDescription(entry: CapabilityEntry): string {
  if (entry.kind === 'plugin') {
    return entry.description;
  }
  return entry.skill?.active?.description ?? entry.skill?.pending?.description ?? entry.description;
}

export function capabilitySource(entry: CapabilityEntry): string {
  if (entry.kind === 'plugin') {
    return entry.plugin ? '本机能力' : entry.install ? '本地包' : '本机能力';
  }
  return '当前工作区';
}

export function capabilityOrigin(entry: CapabilityEntry): string {
  if (entry.kind === 'plugin') {
    return entry.install || entry.plugin?.origin === 'local' ? '用户导入' : '内置';
  }
  return entry.skill?.active?.kind === 'python' ? '兼容包' : '工作区目录';
}

export function capabilityVersion(entry: CapabilityEntry): string {
  if (entry.kind === 'plugin') {
    return entry.plugin?.version || entry.install?.active?.manifest.version || '';
  }
  return entry.skill?.active?.version || entry.skill?.pending?.version || '';
}

export function capabilityError(entry: CapabilityEntry): string {
  if (entry.kind === 'plugin') {
    return entry.plugin?.error || entry.install?.error || '';
  }
  const skill = entry.skill;
  return skill?.candidate_error || skill?.blockers[0] || (skill?.missing ? '源目录缺失' : '')
    || (skill?.uninstalled ? '已移出目录' : '');
}

/* Manager rows are a view over the same inventory used by the Composer bar.
   A removed row stays in the list (for the history toggle) but is not selected. */
export function buildManagerItems(
  plugins: CapabilitySummary[],
  packages: InstalledPackage[],
  skills: ManagedSkill[],
  selectedIds: string[],
): ManagerItem[] {
  const selectedSet = new Set(selectedIds);
  return inventory(plugins, packages, skills).map((entry) => {
    const state = entryState(entry);
    return {
      entry,
      key: entry.key,
      id: entry.id,
      kind: entry.kind,
      kindLabel: kindLabel(entry.kind),
      name: capabilityDisplayName(entry),
      description: capabilityDisplayDescription(entry),
      icon: capabilityIcon(entry.id, entry.kind),
      enabled: state.enabled,
      selected: selectedSet.has(entry.key) && state.enabled,
      statusLabel: state.label,
      tone: state.tone as ManagerTone,
      source: capabilitySource(entry),
      origin: capabilityOrigin(entry),
      version: capabilityVersion(entry),
      error: capabilityError(entry),
      removed: entry.removed,
    };
  });
}

export function baseManagerItems(items: ManagerItem[], history: boolean): ManagerItem[] {
  return history ? items : items.filter((item) => !item.removed);
}

export function filterManagerItems(
  items: ManagerItem[],
  query: string,
  kind: ManagerKind,
  history: boolean,
): ManagerItem[] {
  const needle = query.trim().toLowerCase();
  return baseManagerItems(items, history).filter((item) => {
    if (kind !== 'all' && item.kind !== kind) {
      return false;
    }
    if (!needle) {
      return true;
    }
    return [item.name, item.id, item.description, item.kindLabel, item.source, item.origin]
      .join(' ').toLowerCase().includes(needle);
  });
}

export function managerCounts(items: ManagerItem[], history: boolean): ManagerCounts {
  const base = baseManagerItems(items, history);
  return {
    all: base.length,
    skill: base.filter((item) => item.kind === 'skill').length,
    plugin: base.filter((item) => item.kind === 'plugin').length,
    enabled: base.filter((item) => item.enabled).length,
    selected: base.filter((item) => item.selected && item.enabled).length,
    removed: items.filter((item) => item.removed).length,
  };
}
