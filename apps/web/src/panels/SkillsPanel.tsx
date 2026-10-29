import {CAPABILITY_MANAGER_V2} from '../lib/flags';
import type {SkillsPanelProps} from './capabilityPanelTypes';
import {CapabilityManager} from './capabilities/CapabilityManager';
import {SkillsPanelLegacy} from './SkillsPanelLegacy';

export type {SkillsPanelProps} from './capabilityPanelTypes';

export function SkillsPanel(props: SkillsPanelProps): JSX.Element {
  return CAPABILITY_MANAGER_V2
    ? <CapabilityManager {...props}/>
    : <SkillsPanelLegacy {...props}/>;
}
