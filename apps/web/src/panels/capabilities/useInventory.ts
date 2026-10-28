import {useCallback, useEffect, useRef, useState} from 'react';
import {fetchCapabilities, fetchManagedSkills, type CapabilitySummary, type ManagedSkill} from '../../api';
import {fetchPackages, type InstalledPackage} from '../../extensions/packages';
import {formatBackendError} from '../../lib/format';

export function useInventory(workspaceId: string) {
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
    const serial = ++sequence.current.plugins;
    const skillSerial = includeSkills ? ++sequence.current.skills : sequence.current.skills;
    const results = await Promise.allSettled([fetchCapabilities(), fetchPackages(),
      includeSkills ? fetchManagedSkills(workspaceId) : Promise.resolve(null)]);
    const [capabilities, installs, content] = results;
    if (serial === sequence.current.plugins) {
      if (capabilities.status === 'fulfilled') setPlugins(capabilities.value.capabilities);
      if (installs.status === 'fulfilled') setPackages(installs.value.packages);
      setPluginError([capabilities, installs].filter((r): r is PromiseRejectedResult => r.status === 'rejected').map(r => formatBackendError(r.reason)).join('；'));
    }
    if (skillSerial === sequence.current.skills && content.status === 'fulfilled' && content.value) {setSkills(content.value.skills); setInvalid(content.value.invalid); setSkillsWorkspace(workspaceId);}
    if (includeSkills && skillSerial === sequence.current.skills) setSkillError(content.status === 'rejected' ? formatBackendError(content.reason) : '');
    if (includeSkills && skillSerial === sequence.current.skills) setLoading(false);
  }, [workspaceId]);
  useEffect(() => {
    setSkills([]); setInvalid([]); setSkillError(''); setLoading(true);
    void refresh();
    return () => {sequence.current.plugins++; sequence.current.skills++;};
  }, [refresh]);
  useEffect(() => {
    const timer = window.setInterval(() => {if (!document.hidden) void refresh(false);}, 5000);
    return () => window.clearInterval(timer);
  }, [refresh]);
  return {plugins, packages, skills: skillsWorkspace === workspaceId ? skills : [], invalid: skillsWorkspace === workspaceId ? invalid : [],
    error: [pluginError, skillError].filter(Boolean).join('；'), loading, refresh};
}
