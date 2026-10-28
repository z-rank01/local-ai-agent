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

export function hasContribution(identity: string) {
  return panels.some(entry => entry.id === identity);
}

export function ContributionSettings({identity, ...props}: CapabilityPanelProps & {identity: string}) {
  const entry = panels.find(entry => entry.id === identity);
  if (!entry) return null;
  return <ContributionBoundary title={entry.title}>
    <Suspense fallback={<p className="panel-hint">正在加载设置…</p>}><entry.Panel {...props}/></Suspense>
  </ContributionBoundary>;
}
