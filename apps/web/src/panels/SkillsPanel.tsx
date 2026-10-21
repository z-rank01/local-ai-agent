import {useState} from 'react';
import {Icon} from '../ui/Icon';
import {ContributionPanels} from '../extensions/ContributionPanels';
import type {CapabilityPanelProps} from '../extensions/contracts';
import {CapabilityCatalog} from './CapabilityCatalog';
import {SkillManager} from './SkillManager';

export type SkillsPanelProps = CapabilityPanelProps;

export function SkillsPanel(props: SkillsPanelProps): JSX.Element {
  const [filter, setFilter] = useState('');
  return <>
    <div className="skill-filter">
      <Icon name="i-search" className="ico-sm"/>
      <input className="input input-sm" type="search" autoComplete="off" placeholder="搜索技能…"
        aria-label="搜索技能" value={filter} onChange={event => setFilter(event.target.value)}/>
    </div>
    <ContributionPanels {...props} filter={filter}/>
    <p className="panel-hint">开关即时生效；点卡片就地展开设置。</p>
    <CapabilityCatalog/>
    <SkillManager key={props.conversation?.workspace_id ?? 'default'} workspaceId={props.conversation?.workspace_id ?? 'default'}/>
  </>;
}
