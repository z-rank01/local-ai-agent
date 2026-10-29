import type {CapabilitySummary, ManagedSkill} from '../../api';
import type {InstalledPackage} from '../../extensions/packages';
import {inventory} from './inventory';

export type CapabilityKind = 'skill' | 'plugin';
export type CapabilityStatus = 'ready' | 'loading' | 'unavailable';
export type CapabilityFilter = 'all' | CapabilityKind;

export type CapabilityItem = {
  id: string;
  name: string;
  kind: CapabilityKind;
  description: string;
  icon: string;
  status: CapabilityStatus;
  statusLabel: string;
};

export type ToggleCapability = (item: CapabilityItem, selected: boolean) => void | Promise<void>;

const SKILL_NAMES: Record<string, string> = {
  'meeting-notes': '会议整理',
  'table-reconcile': '表格核对',
  'document-compare': '资料对比',
};

const SKILL_ICONS: Record<string, string> = {
  'meeting-notes': 'i-doc',
  'table-reconcile': 'i-grid',
  'document-compare': 'i-copy',
};

const PLUGIN_ICONS: Record<string, string> = {
  websearch: 'i-wifi',
  finance: 'i-chart',
  'document-batch': 'i-doc',
  'document-index': 'i-search',
  'file-integrity': 'i-shield',
};

const PUBLIC_SOURCES = /^harness-public-sources-v(\d+)$/;
const STOCK_RESEARCH = /^harness-stock-research-v(\d+)$/;
const PLUGIN_STATE_LABELS: Record<string, string> = {
  STARTING: '启动中',
  DRAINING: '等待收尾',
  START_FAILED: '启动失败',
  STOP_FAILED: '收尾未确认',
};

export function kindLabel(kind: CapabilityKind): string {
  return kind === 'skill' ? '技能' : '插件';
}

export function skillDisplayName(id: string): string {
  if (SKILL_NAMES[id]) {
    return SKILL_NAMES[id];
  }
  const publicSources = PUBLIC_SOURCES.exec(id);
  if (publicSources) {
    return `公开来源 v${publicSources[1]}`;
  }
  const stockResearch = STOCK_RESEARCH.exec(id);
  if (stockResearch) {
    return `聊天投研 v${stockResearch[1]}`;
  }
  return id;
}

export function capabilityIcon(id: string, kind: CapabilityKind): string {
  if (kind === 'plugin') {
    return PLUGIN_ICONS[id] ?? 'i-grid';
  }
  if (SKILL_ICONS[id]) {
    return SKILL_ICONS[id];
  }
  if (PUBLIC_SOURCES.test(id)) {
    return 'i-search';
  }
  if (STOCK_RESEARCH.test(id)) {
    return 'i-chart';
  }
  return 'i-spark';
}

function skillState(skill: ManagedSkill): {status: CapabilityStatus; label: string} {
  if (skill.missing) {
    return {status: 'unavailable', label: '源目录缺失'};
  }
  if (skill.uninstalled) {
    return {status: 'unavailable', label: '已移出目录'};
  }
  if (!skill.enabled || !skill.active) {
    // A failed candidate only matters when there is no active revision to run.
    return {status: 'unavailable', label: skill.candidate_error || (skill.enabled ? '待激活' : '已停用')};
  }
  // Pending revisions do not block the active one; an active revision with
  // blockers is not part of the run catalog, so it cannot be mounted.
  if (!skill.pending && skill.blockers.length) {
    return {status: 'unavailable', label: skill.blockers[0]};
  }
  return {status: 'ready', label: '已启用'};
}

function pluginState(plugin?: CapabilitySummary): {status: CapabilityStatus; label: string} {
  if (!plugin) {
    return {status: 'unavailable', label: '未加载'};
  }
  if (plugin.registration === 'invalid') {
    return {status: 'unavailable', label: plugin.error || '清单无效'};
  }
  const states = Object.values(plugin.lifecycle ?? {});
  const failed = states.find((row) => row.state.endsWith('FAILED'));
  if (failed) {
    return {status: 'unavailable', label: PLUGIN_STATE_LABELS[failed.state] ?? '启动失败'};
  }
  const changing = states.find((row) => row.state !== 'ACTIVE' && row.state !== 'INACTIVE');
  if (changing) {
    return {status: 'loading', label: PLUGIN_STATE_LABELS[changing.state] ?? changing.state};
  }
  if (states.length > 0 && states.every((row) => row.accepting)) {
    return {status: 'ready', label: '已激活'};
  }
  return {status: 'unavailable', label: states.length > 0 ? '已停用' : '未加载'};
}

/* Build the task-level library from the same inventory used by the workbench.
   Removed rows are not selectable; every remaining row is visible, including
   unavailable ones, with a short reason instead of a silent disappearance. */
export function buildCapabilityLibrary(
  plugins: CapabilitySummary[],
  packages: InstalledPackage[],
  skills: ManagedSkill[],
): CapabilityItem[] {
  return inventory(plugins, packages, skills)
    .filter((entry) => !entry.removed)
    .map((entry): CapabilityItem => {
      if (entry.kind === 'plugin') {
        const state = pluginState(entry.plugin);
        return {
          id: entry.key,
          name: entry.title || entry.id,
          kind: 'plugin',
          description: entry.description,
          icon: capabilityIcon(entry.id, 'plugin'),
          status: state.status,
          statusLabel: state.label,
        };
      }
      const state = skillState(entry.skill!);
      return {
        id: entry.key,
        name: skillDisplayName(entry.id),
        kind: 'skill',
        // The active revision is what a run would use; a pending revision is
        // only surfaced through its status, never by describing it as current.
        description: entry.skill?.active?.description ?? entry.description,
        icon: capabilityIcon(entry.id, 'skill'),
        status: state.status,
        statusLabel: state.label,
      };
    });
}

export function visibleCapabilities(
  selected: CapabilityItem[],
  maxVisible = 2,
): {visible: CapabilityItem[]; overflow: number} {
  const limit = Math.max(0, Math.floor(maxVisible));
  return {
    visible: selected.slice(0, limit),
    overflow: Math.max(0, selected.length - limit),
  };
}

export function filterCapabilities(
  library: CapabilityItem[],
  query: string,
  kind: CapabilityFilter = 'all',
): CapabilityItem[] {
  const needle = query.trim().toLowerCase();
  return library.filter((item) => {
    if (kind !== 'all' && item.kind !== kind) {
      return false;
    }
    if (!needle) {
      return true;
    }
    return [item.name, kindLabel(item.kind), item.description, item.id]
      .join(' ')
      .toLowerCase()
      .includes(needle);
  });
}

export function toggleSelection(current: string[], id: string, selected: boolean): string[] {
  if (selected) {
    return current.includes(id) ? current : [...current, id];
  }
  return current.filter((value) => value !== id);
}

export function resolveSelected(selectedIds: string[], library: CapabilityItem[]): CapabilityItem[] {
  const byId = new Map(library.map((item) => [item.id, item]));
  return selectedIds
    .map((id) => byId.get(id))
    .filter((item): item is CapabilityItem => Boolean(item));
}

export function resolvedSelectionIds(selectedIds: string[], library: CapabilityItem[]): string[] {
  const available = new Set(library.map((item) => item.id));
  return selectedIds.filter((id) => available.has(id));
}

export function sanitizeRecent(value: unknown): string[] {
  if (!Array.isArray(value)) {
    return [];
  }
  const result: string[] = [];
  for (const item of value) {
    if (typeof item !== 'string' || !item || result.includes(item)) {
      continue;
    }
    result.push(item);
    if (result.length >= 3) {
      break;
    }
  }
  return result;
}

export function rememberRecent(recent: string[], id: string, max = 3): string[] {
  return [id, ...recent.filter((value) => value !== id)].slice(0, Math.max(0, max));
}

/* Recent ordering is applied when the popover opens, not live: adding a row
   must not move the row the user just activated. */
export function sortByRecent(library: CapabilityItem[], recent: string[]): CapabilityItem[] {
  const rank = new Map(recent.map((id, index) => [id, index]));
  const order = new Map(library.map((item, index) => [item.id, index]));
  return [...library].sort((left, right) => {
    const leftRank = rank.get(left.id);
    const rightRank = rank.get(right.id);
    if (leftRank !== undefined || rightRank !== undefined) {
      if (leftRank === undefined) return 1;
      if (rightRank === undefined) return -1;
      if (leftRank !== rightRank) return leftRank - rightRank;
    }
    return (order.get(left.id) ?? 0) - (order.get(right.id) ?? 0);
  });
}
