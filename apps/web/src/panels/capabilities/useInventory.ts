import {useCallback, useEffect, useRef, useState} from 'react';
import {fetchCapabilities, fetchManagedSkills, type CapabilitySummary, type ManagedSkill} from '../../api';
import {fetchPackages, type InstalledPackage} from '../../extensions/packages';
import {formatBackendError} from '../../lib/format';

export function useInventory(
  workspaceId: string,
  options: {poll?: boolean; enabled?: boolean} = {},
) {
  const {poll = true, enabled = true} = options;
  const [plugins, setPlugins] = useState<CapabilitySummary[]>([]);
  const [packages, setPackages] = useState<InstalledPackage[]>([]);
  const [skills, setSkills] = useState<ManagedSkill[]>([]);
  const [skillsWorkspace, setSkillsWorkspace] = useState(workspaceId);
  const [invalid, setInvalid] = useState<{name: string; error: string}[]>([]);
  const [pluginError, setPluginError] = useState('');
  const [skillError, setSkillError] = useState('');
  const [loading, setLoading] = useState(true);
  const sequence = useRef({plugins: 0, skills: 0});
  const refresh = useCallback(async (includeSkills = true) => {
    if (!enabled) return;
    const serial = ++sequence.current.plugins;
    const skillSerial = includeSkills ? ++sequence.current.skills : sequence.current.skills;
    const settle = <T,>(promise: Promise<T>) => promise.then(
      (value) => ({status: 'fulfilled' as const, value}),
      (reason: unknown) => ({status: 'rejected' as const, reason}),
    );
    const contentPromise = includeSkills ? fetchManagedSkills(workspaceId) : Promise.resolve(null);
    // Publish Plugin/package rows as soon as they arrive; a cold Skill runner can
    // take noticeably longer and must not blank the whole capability surface.
    const [capabilities, installs] = await Promise.all([
      settle(fetchCapabilities()),
      settle(fetchPackages()),
    ]);
    if (serial === sequence.current.plugins) {
      if (capabilities.status === 'fulfilled') setPlugins(capabilities.value.capabilities);
      if (installs.status === 'fulfilled') setPackages(installs.value.packages);
      const reasons = [capabilities, installs]
        .filter((row) => row.status === 'rejected')
        .map((row) => formatBackendError((row as {reason: unknown}).reason));
      setPluginError(reasons.join('；'));
    }
    const content = await settle(contentPromise);
    if (skillSerial === sequence.current.skills && content.status === 'fulfilled' && content.value) {setSkills(content.value.skills); setInvalid(content.value.invalid); setSkillsWorkspace(workspaceId);}
    if (includeSkills && skillSerial === sequence.current.skills) setSkillError(content.status === 'rejected' ? formatBackendError(content.reason) : '');
    if (includeSkills && skillSerial === sequence.current.skills) setLoading(false);
  }, [workspaceId, enabled]);
  useEffect(() => {
    setPlugins([]); setPackages([]); setSkills([]); setInvalid([]);
    setPluginError(''); setSkillError(''); setLoading(true);
    if (enabled) void refresh();
    return () => {sequence.current.plugins++; sequence.current.skills++;};
  }, [refresh, enabled]);
  useEffect(() => {
    if (!poll || !enabled) return;
    const timer = window.setInterval(() => {if (!document.hidden) void refresh(false);}, 5000);
    return () => window.clearInterval(timer);
  }, [poll, enabled, refresh]);
  return {plugins, packages, skills: skillsWorkspace === workspaceId ? skills : [], invalid: skillsWorkspace === workspaceId ? invalid : [],
    error: [pluginError, skillError].filter(Boolean).join('；'), loading, refresh};
}
