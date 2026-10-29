import type {CapabilityPanelProps} from '../extensions/contracts';

/* Internal manager props. Selection is deliberately not part of the public
   plugin CapabilityPanelProps: contribution panels keep their old contract. */
export type SkillsPanelProps = CapabilityPanelProps & {
  workspaceId: string;
  selectedCapabilityIds?: string[];
  onCapabilitySelected?: (id: string, selected: boolean) => void;
};
