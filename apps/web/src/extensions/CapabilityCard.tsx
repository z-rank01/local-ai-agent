import {useState, type ReactNode} from 'react';
import {Collapse} from '../ui/Collapse';
import {Icon} from '../ui/Icon';
import {Switch} from '../ui/Switch';
export type SkillCard = {
  id: string;
  icon: string;
  name: string;
  desc: string;
  badge: string;
  badgeClass: string;
  checked: boolean;
  busy: boolean;
  onToggle: (next: boolean) => void;
  body: ReactNode;
};

export function CapabilityCard({card}: {card: SkillCard}) {
  const [open, setOpen] = useState(false);
  return (
            <div className="skill" data-open={open ? 'true' : 'false'}>
              <div className="skill-top">
                <button
                  type="button"
                  className="skill-open"
                  aria-expanded={open}
                  aria-label={`${open ? '收起' : '展开'}${card.name}设置`}
                  onClick={() => setOpen(!open)}
                >
                  <span className="skill-ico">
                    <Icon name={card.icon} />
                  </span>
                  <span className="skill-main">
                    <span className="skill-name">{card.name}</span>
                    <span className="skill-desc">{card.desc}</span>
                  </span>
                  <Icon name="i-chev-down" className="ico-sm skill-chev" />
                </button>
                <span className={card.badgeClass}>{card.badge}</span>
                <Switch
                  checked={card.checked}
                  disabled={card.busy}
                  onChange={(next) => void card.onToggle(next)}
                  label={`启用${card.name}`}
                />
              </div>
              <Collapse open={open} className="skill-body">
                <div className="skill-content">{card.body}</div>
              </Collapse>
            </div>
  );
}
