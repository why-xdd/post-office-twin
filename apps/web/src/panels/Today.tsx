/**
 * «Сегодня» — утро начальника отделения: что сломано, какие осмотры подошли, сколько окон нужно
 * в пик с учётом неисправных и что сделать в первую очередь. Лист печатается на планёрку.
 */

import { CalendarClock, ChevronRight, CircleCheck, LoaderCircle, OctagonAlert, Package, Printer, Users } from 'lucide-react';
import { useEffect, useState } from 'react';
import { ApiError, api } from '../lib/api';
import type { TodayAction, TodayReport } from '../lib/plan';
import { formatNumber } from '../lib/planFacts';
import { formatDate } from '../lib/inspections';
import type { SidePanel } from '../state/store';
import { isDirty, useWorkspace } from '../state/store';
import { useIncidents } from '../state/incidents';
import { useSession } from '../state/session';
import './today.css';

interface TodayProps {
  onFocusObject: (id: string) => void;
  onOpenPanel: (panel: SidePanel) => void;
  onReview: () => void;
}

const ACTION_ICON = {
  incident: OctagonAlert,
  inspection: CalendarClock,
  windows: Users,
  check: OctagonAlert,
  storage: Package,
} as const;

export function Today({ onFocusObject, onOpenPanel, onReview }: TodayProps) {
  const planId = useWorkspace((s) => s.planId);
  const version = useWorkspace((s) => s.version);
  const dirty = useWorkspace(isDirty);
  const incidents = useIncidents((s) => s.items);
  const user = useSession((s) => s.user);
  const [report, setReport] = useState<TodayReport | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!planId) return;
    const controller = new AbortController();
    api
      .today(planId, controller.signal)
      .then((result) => {
        setReport(result);
        setError(null);
      })
      .catch((reason) => {
        if (reason instanceof DOMException && reason.name === 'AbortError') return;
        setError(reason instanceof ApiError ? reason.message : 'Сводка не загрузилась: сервер не ответил.');
      });
    return () => controller.abort();
    // Сводка зависит от сохранённой версии и обращений, а не от каждой правки на плане.
  }, [planId, version, incidents, user]);

  if (!planId) return <p className="today__note">Сводка «Сегодня» появится, когда план будет сохранён.</p>;

  const run = (action: TodayAction) => {
    if (action.kind === 'incident') {
      if (action.object_id) onFocusObject(action.object_id);
      onOpenPanel('incidents');
    } else if (action.kind === 'inspection') {
      if (action.object_id) onFocusObject(action.object_id);
      onOpenPanel('passport');
    } else if (action.kind === 'check') onReview();
    else onOpenPanel('peak');
  };

  const firstName = user ? user.name.split(' ').slice(1).join(' ') : null;

  return (
    <div className="today">
      <header className="today__head">
        <div>
          <p className="today__date">
            {report ? `${capital(report.weekday)}, ${formatDate(report.date)}` : 'Сегодня'}
          </p>
          <h2>{firstName ? `Здравствуйте, ${firstName}` : 'Сводка на сегодня'}</h2>
          {report && <p className="today__plan">{report.plan_title}</p>}
        </div>
        <button type="button" className="icon-btn today__print" onClick={() => window.print()} title="Распечатать сводку">
          <Printer size={17} aria-hidden="true" />
          <span className="visually-hidden">Распечатать</span>
        </button>
      </header>

      {dirty && <p className="today__note">На плане есть несохранённые правки — сводка считает по последней сохранённой версии.</p>}
      {error && <p className="today__error">{error}</p>}
      {!report && !error && (
        <p className="today__note">
          <LoaderCircle size={14} className="spin" aria-hidden="true" /> Собираем сводку…
        </p>
      )}

      {report && (
        <>
          <section className="today__block" aria-labelledby="today-actions">
            <h3 id="today-actions">Что сделать сегодня</h3>
            {report.actions.length === 0 ? (
              <p className="today__ok">
                <CircleCheck size={16} aria-hidden="true" /> Срочных дел нет: всё работает, сроки в порядке, окон хватает.
              </p>
            ) : (
              <ol className="today__actions">
                {report.actions.map((action, index) => {
                  const Icon = ACTION_ICON[action.kind];
                  return (
                    <li key={`${action.kind}-${index}`}>
                      <button type="button" onClick={() => run(action)}>
                        <Icon size={15} aria-hidden="true" className={`today__icon today__icon--${action.kind}`} />
                        <span>{action.text}</span>
                        <ChevronRight size={14} aria-hidden="true" className="today__go" />
                      </button>
                    </li>
                  );
                })}
              </ol>
            )}
          </section>

          <section className="today__block" aria-labelledby="today-windows">
            <h3 id="today-windows">Окна в час пик</h3>
            {report.windows.length === 0 ? (
              <p className="today__note">{report.windows_note ?? 'Нет данных о потоке посетителей.'}</p>
            ) : (
              <table className="today__table">
                <thead>
                  <tr>
                    <th scope="col">Группа</th>
                    <th scope="col">Работает</th>
                    <th scope="col">Нужно</th>
                    <th scope="col">Ожидание</th>
                  </tr>
                </thead>
                <tbody>
                  {report.windows.map((group) => (
                    <tr key={group.name} className={group.working < group.needed ? 'today__row--short' : undefined}>
                      <th scope="row">{group.name}</th>
                      <td>
                        {group.working} из {group.total}
                      </td>
                      <td>{group.needed}</td>
                      <td>{group.mean_wait_min == null ? 'растёт без предела' : `${formatNumber(group.mean_wait_min, 1)} мин`}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
            {report.target_wait_min != null && (
              <p className="today__small">Цель — среднее ожидание не дольше {formatNumber(report.target_wait_min)} мин.</p>
            )}
          </section>

          <section className="today__block" aria-labelledby="today-broken">
            <h3 id="today-broken">Неисправно</h3>
            {report.incidents.length === 0 ? (
              <p className="today__note">Открытых обращений нет.</p>
            ) : (
              <ul className="today__list">
                {report.incidents.map((incident) => (
                  <li key={incident.id}>
                    <button type="button" onClick={() => onFocusObject(incident.object_id)}>
                      <b>{incident.object_label}</b> — {incident.text}
                      <small>
                        {incident.status_label.toLowerCase()}
                        {incident.out_of_service ? ', не работает' : ''}
                      </small>
                    </button>
                  </li>
                ))}
              </ul>
            )}
          </section>

          {/* Сроки осмотров уже стоят в «Что сделать сегодня» и в списке слева — третий раз их не повторяем. */}

          <p className="today__small">
            Нарушений норм на плане: {report.errors}, замечаний: {report.warnings}
            {report.storage_short != null && `. Мест для посылок ${report.storage_short ? `не хватает: ${report.storage_short}` : 'хватает'}`}.
          </p>
        </>
      )}
    </div>
  );
}

const capital = (text: string) => text.charAt(0).toUpperCase() + text.slice(1);
