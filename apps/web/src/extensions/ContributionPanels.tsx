import {Component, Suspense, lazy, type ReactNode} from 'react';
import {contributions} from './generated';
import type {CapabilityPanelProps} from './contracts';

const panels = contributions.map(entry => ({...entry, Panel: lazy(entry.load)}));

class ContributionBoundary extends Component<{title: string; children: ReactNode}, {error: string}> {
  state = {error: ''};
  static getDerivedStateFromError(error: unknown) {
    return {error: error instanceof Error ? error.message : String(error)};
  }
  render() {
    return this.state.error
      ? <p className="run-error" role="alert">{this.props.title}面板不可用：{this.state.error}</p>
      : this.props.children;
  }
}

export function ContributionPanels({filter, ...props}: CapabilityPanelProps & {filter: string}) {
  const keyword = filter.trim().toLowerCase();
  const visible = panels.filter(entry => !keyword || (entry.title + entry.description).toLowerCase().includes(keyword));
  return <div className="skill-list">
    {visible.map(({id, title, Panel}) => <ContributionBoundary key={id} title={title}>
      <Suspense fallback={<p className="panel-hint">正在加载{title}…</p>}><Panel {...props}/></Suspense>
    </ContributionBoundary>)}
    {!visible.length && <p className="ws-empty">没有匹配的技能</p>}
  </div>;
}
