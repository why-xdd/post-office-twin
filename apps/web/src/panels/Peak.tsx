/**
 * Очередь в пик: что будет с отделением, если поток вырастет, окно сломается или часть операций
 * уйдёт в самообслуживание. Ответ — минуты ожидания против цели и люди прямо на плане: где
 * встанет очередь, выйдет ли она за накопитель и насколько сузит проход от входа к окнам.
 */

import { ChevronDown, CircleCheck, LoaderCircle, OctagonAlert, TriangleAlert, Users } from 'lucide-react';
import { useEffect, useMemo, useRef, useState } from 'react';
import { ApiError, api } from '../lib/api';
import type { Equipment, EquipmentTypeInfo, PeakReport, PeakScenario } from '../lib/plan';
import { formatNumber, plural } from '../lib/planFacts';
import { useWorkspace } from '../state/store';
import './peak.css';

/** Что обслуживает посетителей: окна, стойка выдачи, терминалы самообслуживания. */
const SERVICE_TYPES = ['cashier_window', 'pvz_counter', 'self_service_kiosk'];

const FLOW_PRESETS: { value: number; label: string }[] = [
  { value: 1, label: 'Обычный пик' },
  { value: 1.2, label: '+20%' },
  { value: 1.4, label: '+40%' },
  { value: 1.6, label: '+60%' },
];

type WindowState = 'open' | 'closed' | 'broken';

const STATE_LABEL: Record<WindowState, string> = { open: 'Работает', closed: 'Закрыто', broken: 'Неисправно' };

/** Задержка перед пересчётом: ползунок двигают плавно, сервер спрашиваем по паузе. */
const RECALC_DELAY_MS = 250;

function minutes(value: number | null): string {
  if (value === null) return 'без предела';
  if (value >= 120) return 'больше 2 ч';
  return `${formatNumber(value, 1)} мин`;
}

interface PeakProps {
  types: Map<string, EquipmentTypeInfo>;
  /** Неисправные по открытым обращениям сотрудников: окно отмечено сломанным само. */
  reportedBroken?: string[];
}

export function Peak({ types, reportedBroken = [] }: PeakProps) {
  const doc = useWorkspace((s) => s.doc);
  const setPeak = useWorkspace((s) => s.setPeak);
  const [flow, setFlow] = useState(1);
  const [selfService, setSelfService] = useState(false);
  const [share, setShare] = useState(0.5);
  const [states, setStates] = useState<Record<string, WindowState>>({});
  const [report, setReport] = useState<PeakReport | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const abort = useRef<AbortController | null>(null);

  const windows = useMemo<Equipment[]>(
    () => (doc?.equipment ?? []).filter((item) => SERVICE_TYPES.includes(item.type)),
    [doc?.equipment],
  );
  const stateOf = (id: string): WindowState => states[id] ?? (reportedBroken.includes(id) ? 'broken' : 'open');

  const scenario = useMemo<PeakScenario>(
    () => ({
      flow_factor: flow,
      self_service_share: selfService ? share : 0,
      closed: windows.filter((w) => stateOf(w.id) === 'closed').map((w) => w.id),
      out_of_service: windows.filter((w) => stateOf(w.id) === 'broken').map((w) => w.id),
    }),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [flow, selfService, share, states, windows, reportedBroken],
  );

  useEffect(() => {
    if (!doc) return;
    const timer = window.setTimeout(async () => {
      abort.current?.abort();
      const controller = new AbortController();
      abort.current = controller;
      setBusy(true);
      try {
        const result = await api.peak(doc, scenario, controller.signal);
        setReport(result);
        setPeak(result.available ? result : null);
        setError(null);
      } catch (reason) {
        if (reason instanceof DOMException && reason.name === 'AbortError') return;
        setError(reason instanceof ApiError ? reason.message : 'Сервер не ответил. Попробуйте ещё раз.');
      } finally {
        if (abort.current === controller) setBusy(false);
      }
    }, RECALC_DELAY_MS);
    return () => window.clearTimeout(timer);
  }, [doc, scenario, setPeak]);

  // Вкладку закрыли — люди с плана уходят: очередь показана только для сценария.
  useEffect(() => () => {
    abort.current?.abort();
    setPeak(null);
  }, [setPeak]);

  if (!doc) return null;

  const label = (item: Equipment) => item.label || types.get(item.type)?.label || item.type;
  const worst = report?.groups.some((g) => !g.meets_target) || (report?.outside ?? 0) > 0;
  const passageFell =
    report?.passage_with_queue_m != null &&
    report.passage_without_queue_m != null &&
    report.passage_with_queue_m < report.passage_without_queue_m - 0.01;

  return (
    <div className="peak">
      <header className="peak__head">
        <h2>Очередь в пик</h2>
        <p>Меняйте нагрузку и окна — очередь встанет на плане там, где она встанет в зале.</p>
      </header>

      <section className="peak__block" aria-label="Нагрузка">
        <div className="peak__row">
          <label htmlFor="peak-flow" className="peak__label">
            Поток в час пик
          </label>
          <b className="peak__value">{flow === 1 ? 'обычный' : `${flow > 1 ? '+' : ''}${Math.round((flow - 1) * 100)}%`}</b>
        </div>
        <input
          id="peak-flow"
          type="range"
          min={0.5}
          max={2}
          step={0.1}
          value={flow}
          onChange={(event) => setFlow(Number(event.target.value))}
          className="peak__range"
        />
        <div className="peak__presets" role="group" aria-label="Готовые варианты потока">
          {FLOW_PRESETS.map((preset) => (
            <button
              key={preset.value}
              type="button"
              className="peak__preset"
              aria-pressed={Math.abs(flow - preset.value) < 0.01}
              onClick={() => setFlow(preset.value)}
            >
              {preset.label}
            </button>
          ))}
        </div>
        <p className="peak__note">Сезонный пик — с середины ноября по февраль; рост для конкретного отделения задаёте вы.</p>
      </section>

      <section className="peak__block" aria-label="Самообслуживание">
        <label className="peak__switch">
          <input type="checkbox" checked={selfService} onChange={(event) => setSelfService(event.target.checked)} />
          <span>
            <b>Пилот «до 16:00»</b> — стандартные операции уходят в зону самообслуживания
          </span>
        </label>
        {selfService && (
          <>
            <div className="peak__row">
              <label htmlFor="peak-share" className="peak__label">
                Доля операций в самообслуживании
              </label>
              <b className="peak__value">{Math.round(share * 100)}%</b>
            </div>
            <input
              id="peak-share"
              type="range"
              min={0.1}
              max={0.9}
              step={0.05}
              value={share}
              onChange={(event) => setShare(Number(event.target.value))}
              className="peak__range"
            />
          </>
        )}
      </section>

      <section className="peak__block" aria-label="Окна">
        <p className="peak__label">Окна и терминалы</p>
        <ul className="peak__windows">
          {windows.map((item) => (
            <li key={item.id}>
              <span className="peak__window-name">{label(item)}</span>
              <span className="peak__states" role="radiogroup" aria-label={`Состояние: ${label(item)}`}>
                {(['open', 'closed', 'broken'] as WindowState[]).map((state) => (
                  <button
                    key={state}
                    type="button"
                    role="radio"
                    aria-checked={stateOf(item.id) === state}
                    className={`peak__state peak__state--${state}`}
                    onClick={() => setStates((prev) => ({ ...prev, [item.id]: state }))}
                  >
                    {STATE_LABEL[state]}
                  </button>
                ))}
              </span>
            </li>
          ))}
          {windows.length === 0 && <li className="peak__note">На плане нет окон обслуживания.</li>}
        </ul>
        {reportedBroken.length > 0 && (
          <p className="peak__note">Отмеченные неисправными — по открытым обращениям сотрудников.</p>
        )}
      </section>

      <section className={`peak__result${busy ? ' peak__result--busy' : ''}`} aria-live="polite">
        {error && <p className="peak__error">{error}</p>}
        {report && !report.available && <p className="peak__note">{report.reason}</p>}
        {report?.available && (
          <>
            <h3 className="peak__verdict">
              {busy && <LoaderCircle size={16} className="spin" aria-hidden="true" />}
              {worst ? (
                <>
                  <OctagonAlert size={18} aria-hidden="true" className="peak__icon--bad" /> Отделение не справляется
                </>
              ) : (
                <>
                  <CircleCheck size={18} aria-hidden="true" className="peak__icon--ok" /> Цель {report.target_wait_min} мин держится
                </>
              )}
            </h3>
            <ul className="peak__groups">
              {report.groups.map((group) => (
                <li key={group.name} className={`peak__group${group.meets_target ? '' : ' peak__group--bad'}`}>
                  <span className="peak__group-name">
                    {group.name}
                    <small>
                      {group.working.length} из {group.windows.length} работают
                    </small>
                  </span>
                  <span className="peak__group-wait">
                    <b>{minutes(group.mean_wait_min)}</b>
                    <small>ожидание</small>
                  </span>
                  <span className="peak__group-people">
                    <Users size={14} aria-hidden="true" />
                    {group.people} {plural(group.people, 'человек', 'человека', 'человек')}
                  </span>
                </li>
              ))}
            </ul>
            {report.passage_without_queue_m != null && report.passage_with_queue_m != null && (
              <p className={`peak__passage${passageFell ? ' peak__passage--bad' : ''}`}>
                Проход от входа к окнам: {formatNumber(report.passage_without_queue_m)} м
                {passageFell && <> → <b>{formatNumber(report.passage_with_queue_m)} м</b> с очередью</>}
              </p>
            )}
            <ul className="peak__advice">
              {report.advice.map((line) => (
                <li key={line}>
                  {worst ? <TriangleAlert size={14} aria-hidden="true" /> : <CircleCheck size={14} aria-hidden="true" />}
                  <span>{line}</span>
                </li>
              ))}
            </ul>
            <p className="peak__legend">
              <span className="peak__dot" aria-hidden="true" /> в очереди{' '}
              <span className="peak__dot peak__dot--outside" aria-hidden="true" /> не на своём месте
            </p>
            <details className="peak__assumptions">
              <summary>
                Допущения: {report.assumptions.length}
                <ChevronDown size={14} aria-hidden="true" />
              </summary>
              <ul>
                {report.assumptions.map((line) => (
                  <li key={line}>{line}</li>
                ))}
              </ul>
            </details>
          </>
        )}
      </section>
    </div>
  );
}
