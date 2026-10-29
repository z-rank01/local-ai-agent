import {useCallback, useEffect, useMemo, useRef, useState} from 'react';
import {useCapabilityRegistry} from '../lib/capabilityRegistry';
import {readJson, STORAGE_KEYS, writeJson} from '../lib/storage';
import {
  buildCapabilityLibrary,
  rememberRecent,
  resolveSelected,
  resolvedSelectionIds,
  sanitizeRecent,
  sortByRecent,
  type CapabilityItem,
  type ToggleCapability,
} from '../panels/capabilities/capabilityModel';
import {CapabilityBar} from './CapabilityBar';

export type CapabilityBarContainerProps = {
  workspaceId: string;
  conversationId: string | null;
  selectedIds: string[];
  ready: boolean;
  disabled?: boolean;
  shortcutEnabled?: boolean;
  onSelectionChange: (ids: string[]) => void;
  onToggle: ToggleCapability;
  onManage: () => void;
};

export function CapabilityBarContainer({
  workspaceId,
  conversationId,
  selectedIds,
  ready,
  disabled = false,
  shortcutEnabled = true,
  onSelectionChange,
  onToggle,
  onManage,
}: CapabilityBarContainerProps): JSX.Element {
  const data = useCapabilityRegistry();
  const library = useMemo(
    () => buildCapabilityLibrary(data.plugins, data.packages, data.skills),
    [data.packages, data.plugins, data.skills],
  );
  const selected = useMemo(() => resolveSelected(selectedIds, library), [library, selectedIds]);
  const recentRef = useRef<string[]>(sanitizeRecent(readJson(STORAGE_KEYS.capabilityRecent, [])));
  const [recentForSort, setRecentForSort] = useState<string[]>(() => recentRef.current);
  const sortedLibrary = useMemo(() => sortByRecent(library, recentForSort), [library, recentForSort]);
  const normalizedSignature = useRef<string | null>(null);

  useEffect(() => {
    if (!ready || data.loading || data.error || !library.length) {
      return;
    }
    const resolved = resolvedSelectionIds(selectedIds, library);
    if (resolved.length === selectedIds.length) {
      return;
    }
    const signature = `${conversationId ?? 'draft'}:${resolved.join('|')}`;
    if (normalizedSignature.current === signature) {
      return;
    }
    normalizedSignature.current = signature;
    onSelectionChange(resolved);
  }, [conversationId, data.error, data.loading, library, onSelectionChange, ready, selectedIds]);

  const handleOpenChange = useCallback((open: boolean) => {
    if (!open) {
      return;
    }
    setRecentForSort(recentRef.current);
    if (ready) {
      void data.refresh();
    }
  }, [data.refresh, ready]);

  const handleToggle = useCallback((item: CapabilityItem, selectedNext: boolean) => {
    if (selectedNext) {
      const next = rememberRecent(recentRef.current, item.id);
      recentRef.current = next;
      writeJson(STORAGE_KEYS.capabilityRecent, next);
    }
    onToggle(item, selectedNext);
  }, [onToggle]);

  return (
    <CapabilityBar
      selected={selected}
      library={sortedLibrary}
      loading={!ready || (data.loading
        && (library.length === 0 || (selectedIds.length > 0 && selected.length === 0)))}
      error={data.error}
      disabled={disabled}
      shortcutEnabled={shortcutEnabled}
      resetKey={`${workspaceId}:${conversationId ?? 'draft'}`}
      onToggle={handleToggle}
      onRetry={() => { if (ready) void data.refresh(); }}
      onOpenChange={handleOpenChange}
      onManage={onManage}
    />
  );
}
