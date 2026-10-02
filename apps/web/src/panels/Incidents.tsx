/**
 * Обращения о неисправностях: сообщить, взять в работу, закрыть. Сообщить может любой сотрудник
 * (и с QR-бирки без входа), а менять статус — начальник отделения и инженер.
 */

import { ChevronDown, CircleCheck, LoaderCircle, MapPin, OctagonAlert, Send, Wrench } from 'lucide-react';
import { useState } from 'react';
import { ApiError, api } from '../lib/api';
import type { Incident, IncidentStatus } from '../lib/plan';
import { canManage, useSession } from '../state/session';
import { isOpen, useIncidents } from '../state/incidents';
import './incidents.css';

const when = (iso: string) =>
  new Date(iso).toLocaleString('ru-RU', { day: 'numeric', month: 'long', hour: '2-digit', minute: '2-digit' });

const QUICK = ['Не включается', 'Сломан', 'Не печатает', 'Нет связи', 'Повреждён корпус'];

interface ReportFormProps {
  planId: string;
  objectId: string;
  onDone?: (incident: Incident) => void;
  compact?: boolean;
}

export function ReportForm({ planId, objectId, onDone, compact = false }: ReportFormProps) {
  const user = useSession((s) => s.user);
  const upsert = useIncidents((s) => s.upsert);
  const [text, setText] = useState('');
  const [down, setDown] = useState(true);
  const [reporter, setReporter] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [sent, setSent] = useState<Incident | null>(null);

  if (sent) {
    return (
      <div className="report report--sent" role="status">
        <CircleCheck size={18} aria-hidden="true" />
        <div>
          <p>
            <b>Обращение отправлено.</b> Его уже видят начальник отделения и инженер.
          </p>
          <button type="button" className="report__again" onClick={() => setSent(null)}>
            Сообщить ещё о чём-то
          </button>
        </div>
      </div>
    );
  }

  return (
    <form
      className={`report${compact ? ' report--compact' : ''}`}
      onSubmit={async (event) => {
        event.preventDefault();
        if (!text.trim()) {
          setError('Опишите, что случилось, хотя бы двумя словами.');
          return;
        }
        setBusy(true);
        setError(null);
        try {
          const incident = await api.reportIncident(planId, {
            object_id: objectId,
            text: text.trim(),
            out_of_service: down,
            reporter: user ? undefined : reporter.trim() || undefined,
          });
          upsert(incident);
          setSent(incident);
          setText('');
          onDone?.(incident);
        } catch (reason) {
          setError(reason instanceof ApiError ? reason.message : 'Сервер не ответил. Попробуйте ещё раз.');
        } finally {
          setBusy(false);
        }
      }}
    >
      <label className="report__label" htmlFor={`report-${objectId}`}>
        Что случилось
      </label>
      <div className="report__quick">
        {QUICK.map((phrase) => (
          <button key={phrase} type="button" onClick={() => setText((value) => (value ? `${value}, ${phrase.toLowerCase()}` : phrase))}>
            {phrase}
          </button>
        ))}
      </div>
      <textarea
        id={`report-${objectId}`}
        value={text}
        rows={compact ? 2 : 3}
        maxLength={500}
        placeholder="Например: окно не печатает чеки с утра"
        onChange={(event) => setText(event.target.value)}
      />
      <label className="report__check">
        <input type="checkbox" checked={down} onChange={(event) => setDown(event.target.checked)} />
        <span>
          Не работает совсем
          <small>Окно с такой отметкой считается закрытым в расчёте очереди</small>
        </span>
      </label>
      {!user && (
        <label className="report__label">
          Ваше имя <small>(необязательно)</small>
          <input value={reporter} maxLength={80} onChange={(event) => setReporter(event.target.value)} autoComplete="name" />
        </label>
      )}
      {error && (
        <p className="report__error" role="alert">
          {error}
        </p>
      )}
      <button type="submit" className="btn btn--label report__send" disabled={busy}>
        {busy ? <LoaderCircle size={15} className="spin" aria-hidden="true" /> : <Send size={15} aria-hidden="true" />}
        Сообщить о неисправности
      </button>
    </form>
  );
}

const NEXT: Record<IncidentStatus, { status: IncidentStatus; label: string }[]> = {
  new: [
    { status: 'in_progress', label: 'Взять в работу' },
    { status: 'resolved', label: 'Решено' },
  ],
  in_progress: [{ status: 'resolved', label: 'Решено' }],
  resolved: [{ status: 'new', label: 'Открыть снова' }],
};

export function IncidentCard({ incident, onFocus }: { incident: Incident; onFocus?: (id: string) => void }) {
  const user = useSession((s) => s.user);
  const upsert = useIncidents((s) => s.upsert);
  const [busy, setBusy] = useState<IncidentStatus | null>(null);
  const [note, setNote] = useState('');
  const [error, setError] = useState<string | null>(null);
  const manage = canManage(user);
  const last = incident.history[incident.history.length - 1];

  const change = async (status: IncidentStatus) => {
    setBusy(status);
    setError(null);
    try {
      upsert(await api.setIncidentStatus(incident.plan_id, incident.id, status, note));
      setNote('');
    } catch (reason) {
      setError(reason instanceof ApiError ? reason.message : 'Сервер не ответил.');
    } finally {
      setBusy(null);
    }
  };

  return (
    <article className={`incident incident--${incident.status}`}>
      <header className="incident__head">
        <span className="incident__status">{incident.status_label}</span>
        {incident.out_of_service && isOpen(incident) && <span className="incident__down">не работает</span>}
        <time dateTime={incident.created_at}>{when(incident.created_at)}</time>
      </header>
      <p className="incident__object">
        {onFocus ? (
          <button type="button" onClick={() => onFocus(incident.object_id)} title="Показать на плане">
            <MapPin size={13} aria-hidden="true" /> {incident.object_label}
          </button>
        ) : (
          incident.object_label
        )}
      </p>
      <p className="incident__text">{incident.text}</p>
      <p className="incident__by">
        {incident.created_by}
        {last && last.status !== 'new' && (
          <>
            {' · '}
            {last.status === 'resolved' ? 'закрыл(а)' : 'взял(а) в работу'}: {last.by}
            {last.note ? ` — «${last.note}»` : ''}
          </>
        )}
      </p>
      {manage && (
        <div className="incident__actions">
          {incident.status !== 'resolved' && (
            <input
              value={note}
              maxLength={300}
              placeholder="Комментарий (необязательно)"
              onChange={(event) => setNote(event.target.value)}
              aria-label="Комментарий к смене статуса"
            />
          )}
          {NEXT[incident.status].map((next) => (
            <button
              key={next.status}
              type="button"
              className={`btn ${next.status === 'resolved' ? 'btn--label' : 'btn--outline'}`}
              disabled={busy !== null}
              onClick={() => void change(next.status)}
            >
              {busy === next.status && <LoaderCircle size={14} className="spin" aria-hidden="true" />}
              {next.label}
            </button>
          ))}
        </div>
      )}
      {error && <p className="report__error">{error}</p>}
    </article>
  );
}

export function Incidents({ onFocusObject }: { onFocusObject: (id: string) => void }) {
  const items = useIncidents((s) => s.items);
  const error = useIncidents((s) => s.error);
  const user = useSession((s) => s.user);
  const open = items.filter(isOpen);
  const closed = items.filter((i) => !isOpen(i));

  return (
    <div className="incidents">
      <header className="incidents__head">
        <h2>Обращения</h2>
        <p>
          Сотрудник сообщает о неисправности с паспорта объекта или с QR-бирки на нём. Обращения видят все, кто открыл
          план.
        </p>
      </header>
      {!canManage(user) && open.length > 0 && (
        <p className="incidents__hint">Взять в работу и закрыть обращение могут начальник отделения и инженер — после входа.</p>
      )}
      {error && <p className="report__error">{error}</p>}
      {open.length === 0 ? (
        <p className="incidents__empty">
          <Wrench size={16} aria-hidden="true" /> Открытых обращений нет. Чтобы сообщить о неисправности, щёлкните объект на
          плане.
        </p>
      ) : (
        <>
          <p className="incidents__count">
            <OctagonAlert size={15} aria-hidden="true" /> Открыто: <b>{open.length}</b>
          </p>
          <div className="incidents__list">
            {open.map((incident) => (
              <IncidentCard key={incident.id} incident={incident} onFocus={onFocusObject} />
            ))}
          </div>
        </>
      )}
      {closed.length > 0 && (
        <details className="incidents__closed">
          <summary>
            Решённые: {closed.length}
            <ChevronDown size={14} aria-hidden="true" />
          </summary>
          <div className="incidents__list">
            {closed.map((incident) => (
              <IncidentCard key={incident.id} incident={incident} onFocus={onFocusObject} />
            ))}
          </div>
        </details>
      )}
    </div>
  );
}
