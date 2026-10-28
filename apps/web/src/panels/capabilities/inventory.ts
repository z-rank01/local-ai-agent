import type {CapabilitySummary, ManagedSkill} from '../../api';
import type {InstalledPackage} from '../../extensions/packages';

export type CapabilityEntry = {
  key: string; kind: 'plugin' | 'skill'; id: string; title: string; description: string;
  removed: boolean; plugin?: CapabilitySummary; skill?: ManagedSkill; install?: InstalledPackage;
};

export function inventory(plugins: CapabilitySummary[], packages: InstalledPackage[], skills: ManagedSkill[]): CapabilityEntry[] {
  const installed = new Map(packages.map(row => [row.id, row]));
  const rows: CapabilityEntry[] = plugins.map(plugin => ({
    key: 'plugin:' + plugin.id, kind: 'plugin', id: plugin.id, title: plugin.title ?? plugin.id,
    description: plugin.description ?? plugin.error ?? '', plugin, install: installed.get(plugin.id), removed: false,
  }));
  for (const entry of packages) {
    if (plugins.some(row => row.id === entry.id)) continue;
    const manifest = (entry.active ?? entry.pending ?? entry.history[0])?.manifest;
    rows.push({key: 'plugin:' + entry.id, kind: 'plugin', id: entry.id,
      title: manifest?.title ?? entry.id, description: manifest?.description ?? '', install: entry,
      removed: entry.deployment === 'REMOVED'});
  }
  for (const skill of skills) rows.push({key: 'skill:' + skill.id, kind: 'skill', id: skill.id, title: skill.id,
    description: (skill.pending ?? skill.active)?.description ?? skill.candidate_error ?? '', skill,
    removed: Boolean(skill.uninstalled)});
  return rows.sort((a, b) => a.kind.localeCompare(b.kind) || a.title.localeCompare(b.title));
}

export function entryState(entry: CapabilityEntry): {label: string; enabled: boolean; tone: string} {
  if (entry.kind === 'skill') {
    const row = entry.skill!;
    if (row.missing) return {label: '源目录缺失', enabled: false, tone: 'warn'};
    if (row.uninstalled) return {label: '已移出目录', enabled: false, tone: 'quiet'};
    if (row.candidate_error || row.blockers.length) return {label: '需要处理', enabled: Boolean(row.enabled && row.active), tone: 'warn'};
    return {label: row.enabled ? row.active ? '已启用' : '待激活' : '已停用', enabled: Boolean(row.enabled && row.active), tone: row.enabled && row.active ? 'ok' : 'quiet'};
  }
  const states = Object.values(entry.plugin?.lifecycle ?? {});
  const enabled = states.length > 0 && states.every(row => row.accepting);
  const failed = states.some(row => row.state.endsWith('FAILED')) || entry.plugin?.registration === 'invalid';
  const labels: Record<string, string> = {STARTING:'启动中', DRAINING:'等待收尾', START_FAILED:'启动失败', STOP_FAILED:'收尾未确认'};
  const changing = states.find(row => row.state !== 'ACTIVE' && row.state !== 'INACTIVE');
  const label = changing ? labels[changing.state] ?? changing.state : enabled ? '已激活' : entry.plugin ? '已停用' : '未加载';
  return {label, enabled, tone: failed ? 'danger' : enabled ? 'ok' : changing ? 'warn' : 'quiet'};
}
