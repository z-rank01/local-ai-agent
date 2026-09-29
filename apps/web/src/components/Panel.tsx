import {useState, type ReactNode} from 'react';

const PANEL_STORAGE_PREFIX = 'local-ai-agent.panel.';

export function Panel({id, title, actions, defaultCollapsed = false, children}: {
  id: string;
  title: string;
  actions?: ReactNode;
  defaultCollapsed?: boolean;
  children: ReactNode;
}) {
  const storageKey = `${PANEL_STORAGE_PREFIX}${id}`;
  const [collapsed, setCollapsed] = useState(() => {
    const raw = window.localStorage.getItem(storageKey);
    return raw === null ? defaultCollapsed : raw === '1';
  });
  const toggle = () => {
    setCollapsed((current) => {
      const next = !current;
      window.localStorage.setItem(storageKey, next ? '1' : '0');
      return next;
    });
  };
  return (
    <section className={`panel${collapsed ? ' is-collapsed' : ''}`}>
      <div className="panel-header">
        <h2 className="panel-title">
          <button type="button" className="panel-toggle" aria-expanded={!collapsed} onClick={toggle}>
            <span className="panel-chevron" aria-hidden="true">▸</span>
            {title}
          </button>
        </h2>
        {actions ? <div className="panel-actions">{actions}</div> : null}
      </div>
      {collapsed ? null : <div className="panel-body">{children}</div>}
    </section>
  );
}
