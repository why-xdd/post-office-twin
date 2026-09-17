import { CircleCheck, LoaderCircle, RotateCw, WifiOff } from 'lucide-react';
import type { ServerState } from '../lib/useServer';
import './server-badge.css';

interface ServerBadgeProps {
  state: ServerState;
  onRetry: () => void;
}

const TEXT: Record<ServerState, string> = {
  checking: 'Подключение к серверу',
  waking: 'Сервер просыпается — до минуты',
  ready: 'Сервер на связи',
  offline: 'Сервер недоступен',
};

export function ServerBadge({ state, onRetry }: ServerBadgeProps) {
  return (
    <div className={`server-badge server-badge--${state}`} role="status" aria-live="polite">
      {state === 'ready' && <CircleCheck size={16} aria-hidden="true" />}
      {(state === 'checking' || state === 'waking') && (
        <LoaderCircle size={16} className="server-badge__spin" aria-hidden="true" />
      )}
      {state === 'offline' && <WifiOff size={16} aria-hidden="true" />}
      <span>{TEXT[state]}</span>
      {state === 'offline' && (
        <button type="button" className="server-badge__retry" onClick={onRetry}>
          <RotateCw size={14} aria-hidden="true" />
          Повторить
        </button>
      )}
    </div>
  );
}
