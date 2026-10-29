import {createContext, useContext} from 'react';
import type {useInventory} from '../panels/capabilities/useInventory';

export type CapabilityRegistry = ReturnType<typeof useInventory>;

/* One registry for the workbench and the composer bar. The Sheet is mounted for
   the whole session, so sharing its polling avoids duplicate Skill freezes and
   keeps the bar in step with enable/disable/import actions. */
export const CapabilityRegistryContext = createContext<CapabilityRegistry | null>(null);

export function useCapabilityRegistry(): CapabilityRegistry {
  const value = useContext(CapabilityRegistryContext);
  if (!value) {
    throw new Error('CapabilityRegistryContext is missing');
  }
  return value;
}
