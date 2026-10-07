import {useNotice} from '../lib/notify';
import {Icon} from './Icon';

export function Toast(): JSX.Element | null {
  const notice = useNotice();
  if (!notice) {
    return null;
  }
  return (
    <div className="toast" key={notice.id} role="status" aria-live="polite">
      <Icon name={notice.kind === 'error' ? 'i-alert' : 'i-check'} className="ico-sm" />
      <span>{notice.text}</span>
    </div>
  );
}
