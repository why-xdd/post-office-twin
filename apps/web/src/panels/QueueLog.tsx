/**
 * Очередь по фактическим данным отделения: журнал электронной очереди или почасовой счётчик
 * посетителей. По каждой группе окон — поток по часам и часы, в которые окон на плане не хватает.
 */

import { ChevronDown, FileClock, Upload } from 'lucide-react';
import { useRef, useState } from 'react';
import { ApiError, api } from '../lib/api';
import type { Plan, QueueGroupHours, QueueHour, QueueLogReport } from '../lib/plan';
import { formatNumber, plural } from '../lib/planFacts';

interface LogState {
  busy: boolean;
  error: string | null;
  report: QueueLogReport | null;
  checked: Plan | null;
}

// Расчёт по журналу переживает переключение вкладок, пока открыт тот же план.
const lastLog = new Map<string, LogState>();

const CHART_HEIGHT = 96;
const LABEL_ROOM = 16;

function hourLabel(hour: number): string {
  return `${String(hour).padStart(2, '0')}:00`;
}

function describe(hour: QueueHour, windows: number): string {
  const parts = [`${hourLabel(hour.hour)}: ${formatNumber(hour.arrivals_per_hour, 1)} посетителей в час`];
  if (hour.measured_wait_min !== null && hour.measured_wait_min !== undefined) {
    parts.push(`ожидание по журналу ${formatNumber(hour.measured_wait_min, 1)} мин`);
  }
  parts.push(hour.stable ? `по модели ${formatNumber(hour.model_wait_min ?? 0, 1)} мин` : 'по модели очередь растёт без предела');
  parts.push(hour.short ? `нужно окон: ${hour.windows_needed}, на плане ${windows}` : `окон хватает (нужно ${hour.windows_needed})`);
  return parts.join('; ');
}

function HourChart({ group }: { group: QueueGroupHours }) {
  const [active, setActive] = useState<number | null>(null);
  const byHour = new Map(group.hours.map((h) => [h.hour, h]));
  const first = Math.min(...group.hours.map((h) => h.hour));
  const last = Math.max(...group.hours.map((h) => h.hour));
  // Ось — все часы подряд: час без данных остаётся пустым местом, а не исчезает.
  const span = Array.from({ length: last - first + 1 }, (_, index) => first + index);
  const max = Math.max(...group.hours.map((h) => h.arrivals_per_hour), 1);
  const step = 100 / span.length;
  const current = active === null ? null : byHour.get(active) ?? null;
  return (
    <figure className="qlog__chart">
      <div className="qlog__plot" style={{ height: CHART_HEIGHT + LABEL_ROOM }} onMouseLeave={() => setActive(null)}>
        {span.map((hourNumber, index) => {
          const hour = byHour.get(hourNumber);
          if (!hour) return null;
          const height = Math.max(2, (hour.arrivals_per_hour / max) * CHART_HEIGHT);
          return (
            <span
              key={hourNumber}
              role="img"
              tabIndex={0}
              className={`qlog__bar${hour.short ? ' qlog__bar--short' : ''}${active === hourNumber ? ' qlog__bar--active' : ''}`}
              style={{ left: `${index * step}%`, width: `${step}%` }}
              onMouseEnter={() => setActive(hourNumber)}
              onFocus={() => setActive(hourNumber)}
              onBlur={() => setActive(null)}
              aria-label={describe(hour, group.windows_on_plan)}
            >
              {hour.short && <span className="qlog__flag">нужно {hour.windows_needed}</span>}
              <span className="qlog__fill" style={{ height }} />
            </span>
          );
        })}
      </div>
      <div className="qlog__axis" aria-hidden="true">
        {span.map((hourNumber, index) => (
          <span key={hourNumber} style={{ left: `${(index + 0.5) * step}%` }}>
            {hourNumber % 2 === 0 ? String(hourNumber).padStart(2, '0') : ''}
          </span>
        ))}
      </div>
      <figcaption className="qlog__tip">
        {current ? describe(current, group.windows_on_plan) : 'Посетителей в час по часам работы. Наведите на столбец, чтобы увидеть ожидание.'}
      </figcaption>
    </figure>
  );
}

function GroupBlock({ group }: { group: QueueGroupHours }) {
  const hasLog = group.hours.some((h) => h.measured_wait_min !== null && h.measured_wait_min !== undefined);
  return (
    <div className="qlog__group">
      <h4>
        {group.name}
        <span>
          окон на плане: {group.windows_on_plan} · обслуживание {formatNumber(group.service_min, 1)} мин ({group.service_source})
        </span>
      </h4>
      <HourChart group={group} />
      <details className="rec__assumptions">
        <summary>
          Таблица по часам
          <ChevronDown size={14} aria-hidden="true" />
        </summary>
        <table className="capacity">
          <thead>
            <tr>
              <th scope="col">Час</th>
              <th scope="col">Поток</th>
              {hasLog && <th scope="col">Ждали</th>}
              <th scope="col">Модель</th>
              <th scope="col">Окон нужно</th>
            </tr>
          </thead>
          <tbody>
            {group.hours.map((hour) => (
              <tr key={hour.hour}>
                <th scope="row">{hourLabel(hour.hour)}</th>
                <td>{formatNumber(hour.arrivals_per_hour, 1)}</td>
                {hasLog && <td>{hour.measured_wait_min === null || hour.measured_wait_min === undefined ? '—' : `${formatNumber(hour.measured_wait_min, 1)} мин`}</td>}
                <td>{hour.stable ? `${formatNumber(hour.model_wait_min ?? 0, 1)} мин` : 'без предела'}</td>
                <td className={hour.short ? 'capacity--hot' : ''}>{hour.windows_needed}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </details>
    </div>
  );
}

export function QueueLog({ doc }: { doc: Plan }) {
  const input = useRef<HTMLInputElement>(null);
  const planKey = doc.ops.id;
  const [state, setLogState] = useState<LogState>(
    () => lastLog.get(planKey) ?? { busy: false, error: null, report: null, checked: null },
  );
  const setState = (next: LogState) => {
    lastLog.set(planKey, { ...next, busy: false });
    setLogState(next);
  };

  const load = async (file: File) => {
    setState({ busy: true, error: null, report: null, checked: null });
    try {
      setState({ busy: false, error: null, report: await api.queueLog(doc, file), checked: doc });
    } catch (error) {
      setState({ busy: false, error: error instanceof ApiError ? error.message : 'Не удалось разобрать файл', report: null, checked: null });
    }
  };

  const report = state.report;
  return (
    <section className="assistant__section">
      <header className="assistant__section-head">
        <h3>
          <FileClock size={15} aria-hidden="true" /> Очередь по данным отделения
        </h3>
      </header>
      <p className="assistant__hint">
        Журнал электронной очереди (талон: выдан, вызван, завершён) или почасовой счётчик посетителей в CSV. Покажем, в
        какие часы окон на плане не хватает и сколько окон держать открытыми.
      </p>
      <div>
        <button type="button" className="btn btn--outline" onClick={() => input.current?.click()} disabled={state.busy}>
          <Upload size={15} aria-hidden="true" />
          {state.busy ? 'Считаем…' : 'Загрузить журнал очереди'}
        </button>
        <input
          ref={input}
          type="file"
          accept=".csv,.txt,text/csv"
          hidden
          onChange={(event) => {
            const file = event.target.files?.[0];
            event.target.value = '';
            if (file) void load(file);
          }}
        />
      </div>
      {state.error && (
        <p className="ledger__error" role="alert">
          {state.error}
        </p>
      )}
      <div aria-live="polite">
        {report && (
          <div className="qlog">
            {state.checked !== doc && (
              <p className="ledger__stale">План изменён после расчёта — загрузите журнал ещё раз, чтобы учесть текущие окна.</p>
            )}
            <p className="qlog__source">
              {report.source === 'журнал электронной очереди' ? 'Журнал очереди' : 'Счётчик посетителей'}: {report.records}{' '}
              записей за {report.days} {plural(report.days, 'день', 'дня', 'дней')}
              {report.skipped_rows > 0 && `, пропущено строк: ${report.skipped_rows}`}. Часы пик по данным:{' '}
              {report.peak_hours.join(', ') || '—'}.
            </p>
            <ul className="qlog__summary">
              {report.summary.map((line, index) => (
                <li key={index}>{line}</li>
              ))}
            </ul>
            {report.groups.map((group) => (
              <GroupBlock key={group.name} group={group} />
            ))}
            {report.assumptions.length > 0 && (
              <details className="rec__assumptions">
                <summary>
                  Допущения расчёта: {report.assumptions.length}
                  <ChevronDown size={14} aria-hidden="true" />
                </summary>
                <ul>
                  {report.assumptions.map((text, index) => (
                    <li key={index}>{text}</li>
                  ))}
                </ul>
              </details>
            )}
          </div>
        )}
      </div>
    </section>
  );
}
