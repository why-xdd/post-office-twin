/**
 * Ассистент планировки.
 *
 * Рекомендации считает сервер: перебирает допустимые места, оценивает их нормами и считает
 * эффект до и после. Здесь каждая рекомендация показывает, что изменится, почему и с какими
 * допущениями; «Показать на плане» выдвигает объект на новое место, «Принять» применяет
 * изменение одной правкой, которую можно отменить.
 */

import {
  ArrowRight,
  Check,
  ChevronDown,
  Eye,
  EyeOff,
  LoaderCircle,
  MapPinned,
  Copy,
  FileText,
  MessageSquare,
  Sparkles,
  TriangleAlert,
  Users,
} from 'lucide-react';
import { useEffect, useMemo, useRef, useState } from 'react';
import { Plate } from '../components/Plate';
import { SelectField } from '../components/fields';
import type { AddressBook } from '../lib/addresses';
import { formatAddress, key } from '../lib/addresses';
import { ApiError, api } from '../lib/api';
import { mergeSteps } from '../lib/fixProposal';
import { humanize } from '../lib/humanize';
import type {
  CapacityReport,
  CommandResult,
  Conclusion as ConclusionText,
  Effect,
  EquipmentTypeInfo,
  ImprovementReport,
  Recommendation,
  RecommendationChange,
} from '../lib/plan';
import { formatNumber, plural } from '../lib/planFacts';
import { useWorkspace } from '../state/store';
import { QueueLog } from './QueueLog';
import './assistant.css';

interface AssistantProps {
  book: AddressBook;
  types: EquipmentTypeInfo[];
  onFocusObject: (id: string) => void;
  onNotice: (text: string) => void;
}

type Job = 'advise' | 'improve' | 'place' | 'command' | 'conclusion' | null;

const SCENARIO_LABEL: Record<string, string> = {
  fix: 'Исправление',
  missing: 'Не хватает',
  excess: 'Лишнее',
  capacity: 'Пропускная способность',
  placement: 'Размещение',
};

export function Assistant({ book, types, onFocusObject, onNotice }: AssistantProps) {
  const doc = useWorkspace((s) => s.doc);
  const setPreview = useWorkspace((s) => s.setPreview);
  const applyChanges = useWorkspace((s) => s.applyChanges);
  const highlight = useWorkspace((s) => s.highlight);

  const [job, setJob] = useState<Job>(null);
  const [error, setError] = useState<string | null>(null);
  const [recommendations, setRecommendations] = useState<Recommendation[] | null>(null);
  // Откуда список: ответ на команду показывается прямо под полем ввода, подбор — ниже кнопок.
  const [recsFrom, setRecsFrom] = useState<'command' | 'advise'>('advise');
  const [capacity, setCapacity] = useState<CapacityReport | null>(null);
  const [improvement, setImprovement] = useState<ImprovementReport | null>(null);
  // Подбор остановлен по времени: об этом нужно сказать, иначе список выглядит полным.
  const [partial, setPartial] = useState(false);
  const [placeType, setPlaceType] = useState('storage_rack');
  const [placeResult, setPlaceResult] = useState<Recommendation | null>(null);
  const [previewId, setPreviewId] = useState<string | null>(null);
  const [stale, setStale] = useState(false);
  const [command, setCommand] = useState('');
  const [commandResult, setCommandResult] = useState<CommandResult | null>(null);
  const [conclusion, setConclusion] = useState<ConclusionText | null>(null);
  const docAtRun = useRef<string>('');
  const [elapsed, setElapsed] = useState(0);

  // Результаты считаются для конкретного состояния плана; после правки они помечаются устаревшими.
  useEffect(() => {
    if (!doc || !docAtRun.current) return;
    setStale(JSON.stringify(doc) !== docAtRun.current);
  }, [doc]);

  useEffect(() => {
    if (!job) return;
    const started = Date.now();
    setElapsed(0);
    const timer = window.setInterval(() => setElapsed(Math.floor((Date.now() - started) / 1000)), 500);
    return () => window.clearInterval(timer);
  }, [job]);

  useEffect(() => () => setPreview(null), [setPreview]);

  const run = async <T,>(kind: Job, request: () => Promise<T>, onResult: (result: T) => void) => {
    if (!doc) return;
    setJob(kind);
    setError(null);
    setPreview(null);
    setPreviewId(null);
    try {
      const result = await request();
      docAtRun.current = JSON.stringify(useWorkspace.getState().doc);
      setStale(false);
      onResult(result);
    } catch (reason) {
      setError(reason instanceof ApiError ? reason.message : 'Сервер не ответил. Попробуйте ещё раз.');
    } finally {
      setJob(null);
    }
  };

  const advise = () =>
    run('advise', () => api.assistant(doc!), (result) => {
      setRecommendations(result.recommendations);
      setRecsFrom('advise');
      setCapacity(result.capacity);
      setImprovement(null);
      setPartial(Boolean(result.partial));
    });

  const improve = () =>
    run('improve', () => api.improve(doc!), (result) => {
      setImprovement(result);
    });

  const runCommand = () => {
    const text = command.trim();
    if (!text) return;
    void run('command', () => api.command(doc!, text), (result) => {
      setCommandResult(result);
      // Рекомендации команды показываются тем же списком: у них та же карточка и то же «Принять».
      setRecommendations(result.recommendations);
      setRecsFrom('command');
      setImprovement(null);
      setPartial(false);
    });
  };

  const buildConclusion = () =>
    run('conclusion', () => api.conclusion(doc!), (result) => setConclusion(result));

  const place = () =>
    run('place', () => api.place(doc!, placeType), (result) => {
      setPlaceResult(result);
    });

  const togglePreview = (id: string, changes: RecommendationChange[]) => {
    if (previewId === id) {
      setPreview(null);
      setPreviewId(null);
      highlight([]);
      return;
    }
    setPreview(changes);
    setPreviewId(id);
    const first = changes.find((c) => c.position)?.object_id ?? changes[0]?.object_id;
    if (first) onFocusObject(first);
  };

  const accept = (address: string, changes: RecommendationChange[]) => {
    applyChanges(changes);
    setPreview(null);
    setPreviewId(null);
    highlight([]);
    onNotice(`Рекомендация ${address} принята. Отменить — Ctrl+Z`);
    useWorkspace.getState().latch(changes.map((c) => c.object_id));
  };

  const improvementChanges = useMemo(() => (improvement ? mergeSteps(improvement) : []), [improvement]);

  const typeOptions = types.filter((t) => t.floor_object).map((t) => ({ value: t.code, label: t.label }));

  const recommendationList = recommendations && (
    <section className="assistant__section">
          <header className="assistant__section-head">
            <h3>Рекомендации</h3>
            <span>
              {recommendations.length} {plural(recommendations.length, 'рекомендация', 'рекомендации', 'рекомендаций')}
            </span>
          </header>
          {partial && recommendations.length > 0 && (
            <p className="assistant__stale">
              Подбор остановлен по отведённому времени: показано то, что сервис успел найти. Это
              бывает на очень больших залах — важные рекомендации считаются первыми.
            </p>
          )}
          {recommendations.length === 0 ? (
            <p className="assistant__empty">Рекомендаций нет: ошибок проверки нет, комплектность и пропускная способность в норме.</p>
          ) : (
            <ol className="recs">
              {recommendations.map((rec, index) => {
                const address = formatAddress('recommendation', index);
                return (
                  <RecommendationCard
                    key={rec.id}
                    address={address}
                    rec={rec}
                    book={book}
                    previewing={previewId === rec.id}
                    onPreview={() => togglePreview(rec.id, rec.changes)}
                    onAccept={() => accept(address, rec.changes)}
                    onObjects={(ids) => highlight(ids)}
                  />
                );
              })}
            </ol>
          )}
        </section>
  );

  return (
    <div className="assistant">
      <section className="assistant__intro">
        <h2>Ассистент планировки</h2>
        <p>
          Рекомендации рассчитаны по геометрии плана и нормам: каждая показывает изменение на плане, основание со
          ссылкой на документ и эффект до и после.
        </p>
        <form
          className="assistant__command"
          onSubmit={(event) => {
            event.preventDefault();
            runCommand();
          }}
        >
          <label htmlFor="assistant-command">Скажите, что сделать, или спросите о плане</label>
          <div className="assistant__command-row">
            <input
              id="assistant-command"
              value={command}
              placeholder="поставь почтовый ящик у входа"
              onChange={(event) => setCommand(event.target.value)}
              disabled={job !== null}
            />
            <button type="submit" className="btn btn--steel-light" disabled={job !== null || !command.trim()}>
              {job === 'command' ? <LoaderCircle size={16} className="spin" aria-hidden="true" /> : <MessageSquare size={16} aria-hidden="true" />}
              Понять
            </button>
          </div>
          <p className="assistant__hint">
            Понимает и вопросы: «сколько окон обслуживания», «где стоит банкомат», «какая
            свободная площадь». План меняется только после «Принять».
          </p>
        </form>
        {commandResult && (
          <div className="assistant__understood">
            {commandResult.understood.length > 0 && (
              <p>
                <b>Понято</b> — {humanize(commandResult.understood.join('; '), doc, book)}{' '}
                {/* Свой разбор — обычный путь; называем источник, только если фразу разобрала языковая модель. */}
                {commandResult.source !== 'разбор фразы' && <span>({commandResult.source})</span>}
              </p>
            )}
            {(commandResult.answers ?? []).map((answer) => (
              <p key={answer} className="assistant__answer">
                {humanize(answer, doc, book)}
              </p>
            ))}
            {commandResult.unknown.length > 0 && (
              <p className="assistant__unknown">Не понято: {commandResult.unknown.join('; ')}</p>
            )}
          </div>
        )}
        {recsFrom === 'command' && recommendationList}

        <div className="assistant__run">
          <button type="button" className="btn btn--steel-light" onClick={advise} disabled={job !== null || !doc}>
            {job === 'advise' ? <LoaderCircle size={16} className="spin" aria-hidden="true" /> : <Sparkles size={16} aria-hidden="true" />}
            Подобрать рекомендации
          </button>
          <button type="button" className="btn btn--outline" onClick={improve} disabled={job !== null || !doc}>
            {job === 'improve' ? <LoaderCircle size={16} className="spin" aria-hidden="true" /> : <ArrowRight size={16} aria-hidden="true" />}
            Улучшить пошагово
          </button>
        </div>
        {job && (
          <p className="assistant__working" role="status">
            {job === 'improve'
              ? `Перебираем переносы и проверяем каждый вариант нормами — ${elapsed} с, обычно до 15 с`
              : `Считаем варианты — ${elapsed} с`}
          </p>
        )}
        {error && (
          <p className="assistant__error" role="alert">
            <TriangleAlert size={15} aria-hidden="true" />
            {error}
          </p>
        )}
        {stale && (recommendations || improvement || placeResult) && (
          <p className="assistant__stale">
            План изменился после расчёта — рекомендации могут не соответствовать текущему состоянию.{' '}
            <button type="button" onClick={improvement ? improve : advise}>
              Пересчитать
            </button>
          </p>
        )}
      </section>

      {improvement && (
        <section className="assistant__section">
          <header className="assistant__section-head">
            <h3>Пошаговое улучшение</h3>
            <span>
              {improvement.steps.length} {plural(improvement.steps.length, 'шаг', 'шага', 'шагов')} ·{' '}
              {formatNumber(improvement.elapsed_ms / 1000, 1)} с
            </span>
          </header>
          {improvement.partial && (
            <p className="assistant__stale">
              Подбор остановлен по отведённому времени: показаны шаги, которые сервис успел найти.
              Повторный запуск продолжит улучшение с этого плана.
            </p>
          )}
          <EffectTable effects={improvement.effects} />
          <ol className="steps">
            {improvement.steps.map((step, index) => (
              <li key={step.recommendation.id + index}>
                <Plate surface="label" size="sm">
                  {`Ш-${String(index + 1).padStart(2, '0')}`}
                </Plate>
                <span className="steps__title">{humanize(step.recommendation.title, doc, book)}</span>
                <span className={`steps__errors${step.errors_after === 0 ? ' steps__errors--zero' : ''}`}>
                  {step.errors_after} {plural(step.errors_after, 'ошибка', 'ошибки', 'ошибок')}
                </span>
              </li>
            ))}
          </ol>
          {improvementChanges.length > 0 && (
            <div className="rec__actions">
              <button
                type="button"
                className="btn btn--outline"
                aria-pressed={previewId === 'improve'}
                onClick={() => togglePreview('improve', improvementChanges)}
              >
                {previewId === 'improve' ? <EyeOff size={15} aria-hidden="true" /> : <Eye size={15} aria-hidden="true" />}
                {previewId === 'improve' ? 'Скрыть с плана' : 'Показать итог на плане'}
              </button>
              <button type="button" className="btn btn--brass" onClick={() => accept('всех шагов', improvementChanges)}>
                <Check size={16} aria-hidden="true" />
                Принять все шаги
              </button>
            </div>
          )}
        </section>
      )}

      {recsFrom === 'advise' && recommendationList}

      {capacity && <CapacityBlock capacity={capacity} />}

      {/* Редкие инструменты свёрнуты: на экране одно решение за раз — команда и подбор. */}
      <details className="assistant__more">
        <summary>
          Ещё: заключение для сдачи, место для объекта, журнал очереди
          <ChevronDown size={15} aria-hidden="true" />
        </summary>
        <section className="assistant__section">
          <div className="assistant__section-head">
            <h3>
              <FileText size={15} aria-hidden="true" /> Заключение по плану
            </h3>
          </div>
          <p className="assistant__hint">
            Связный текст для сдачи: что проверено, что нарушено, что предлагается и каким будет эффект. Все числа берутся
            из отчёта проверки.
          </p>
          <button type="button" className="btn btn--outline" onClick={buildConclusion} disabled={job !== null || !doc}>
            {job === 'conclusion' ? <LoaderCircle size={16} className="spin" aria-hidden="true" /> : <FileText size={16} aria-hidden="true" />}
            Собрать заключение
          </button>
          {conclusion && (
            <div className="conclusion">
              <p className="conclusion__text">{conclusion.text}</p>
              <div className="conclusion__foot">
                <span>Собрано по расчётам плана</span>
                <button
                  type="button"
                  className="btn btn--outline"
                  onClick={() => {
                    void navigator.clipboard?.writeText(conclusion.text);
                    onNotice('Заключение скопировано');
                  }}
                >
                  <Copy size={14} aria-hidden="true" />
                  Скопировать
                </button>
              </div>
            </div>
          )}
        </section>

        {doc && <QueueLog doc={doc} />}

        <section className="assistant__section">
          <header className="assistant__section-head">
            <h3>Найти место для объекта</h3>
          </header>
          <p className="assistant__hint">
            Ассистент переберёт позиции в допустимых для типа зонах и оставит те, где объект не нарушает ни одной нормы.
          </p>
          <div className="assistant__place">
            <SelectField label="Что поставить" wide value={placeType} options={typeOptions} onCommit={setPlaceType} />
            <button type="button" className="btn btn--steel-light" onClick={place} disabled={job !== null}>
              {job === 'place' ? <LoaderCircle size={16} className="spin" aria-hidden="true" /> : <MapPinned size={16} aria-hidden="true" />}
              Найти место
            </button>
          </div>
          {placeResult && (
            <ol className="recs">
              <RecommendationCard
                address="Р-М"
                rec={placeResult}
                book={book}
                previewing={previewId === 'place'}
                onPreview={() => togglePreview('place', placeResult.changes)}
                onAccept={() => {
                  accept('по размещению', placeResult.changes);
                  setPlaceResult(null);
                }}
                onObjects={(ids) => highlight(ids)}
              />
            </ol>
          )}
        </section>
      </details>
    </div>
  );
}

function RecommendationCard({
  address,
  rec,
  book,
  previewing,
  onPreview,
  onAccept,
  onObjects,
}: {
  address: string;
  rec: Recommendation;
  book: AddressBook;
  previewing: boolean;
  onPreview: () => void;
  onAccept: () => void;
  onObjects: (ids: string[]) => void;
}) {
  const doc = useWorkspace((s) => s.doc);
  const actionable = rec.changes.length > 0;
  // Рекомендация, которая добавляет нарушений или замечаний, предупреждает об этом до «Принять».
  const worse = rec.effects.filter(
    (effect) =>
      (effect.key === 'errors' || effect.key === 'warnings') &&
      effect.before !== null &&
      effect.after !== null &&
      effect.after > effect.before,
  );
  return (
    <li
      className={`rec${previewing ? ' rec--previewing' : ''}`}
      onMouseEnter={() => onObjects(rec.objects)}
      onMouseLeave={() => onObjects([])}
    >
      <div className="rec__head">
        <Plate surface="label" size="sm">
          {address}
        </Plate>
        <span className="rec__scenario">{SCENARIO_LABEL[rec.scenario] ?? rec.scenario}</span>
      </div>
      <h4 className="rec__title">{humanize(rec.title, doc, book)}</h4>
      <p className="rec__summary">{humanize(rec.summary, doc, book)}</p>

      {rec.effects.length > 0 && <EffectTable effects={rec.effects} />}

      {rec.reasons.length > 0 && (
        <ul className="rec__reasons">
          {rec.reasons.map((reason, index) => (
            <li key={index}>
              {humanize(reason.text, doc, book)}
              {reason.source && (
                <cite>
                  {reason.source}
                  {reason.clause ? `, ${reason.clause}` : ''}
                </cite>
              )}
            </li>
          ))}
        </ul>
      )}

      {rec.assumptions.length > 0 && (
        <details className="rec__assumptions">
          <summary>
            Допущения: {rec.assumptions.length}
            <ChevronDown size={14} aria-hidden="true" />
          </summary>
          <ul>
            {rec.assumptions.map((text, index) => (
              <li key={index}>{humanize(text, doc, book)}</li>
            ))}
          </ul>
        </details>
      )}

      {rec.changes.length > 0 && (
        <p className="rec__changes">
          {rec.changes.map((change) => (
            <span key={change.object_id + change.op}>
              {OP_LABEL[change.op]} {book.byId.get(key('equipment', change.object_id)) ?? 'новый объект'}
            </span>
          ))}
        </p>
      )}

      {actionable && worse.length > 0 && (
        <p className="rec__caution" role="note">
          <TriangleAlert size={14} aria-hidden="true" />
          <span>
            После «Принять» станет больше:{' '}
            {worse.map((effect) => `${effect.label.toLowerCase()} ${effect.before} → ${effect.after}`).join(', ')}.
            Посмотрите на плане, какие именно, прежде чем принимать.
          </span>
        </p>
      )}

      {actionable && (
        <div className="rec__actions">
          <button type="button" className="btn btn--outline" aria-pressed={previewing} onClick={onPreview}>
            {previewing ? <EyeOff size={15} aria-hidden="true" /> : <Eye size={15} aria-hidden="true" />}
            {previewing ? 'Скрыть с плана' : 'Показать на плане'}
          </button>
          <button type="button" className="btn btn--brass" onClick={onAccept}>
            <Check size={16} aria-hidden="true" />
            Принять
          </button>
        </div>
      )}
    </li>
  );
}

const OP_LABEL: Record<string, string> = { add: 'добавить', move: 'перенести', remove: 'убрать' };

function EffectTable({ effects }: { effects: Effect[] }) {
  const shown = effects.filter((e) => e.before !== e.after || e.key === 'errors');
  if (shown.length === 0) return null;
  return (
    <table className="effects">
      <caption className="visually-hidden">Показатели до и после</caption>
      <thead>
        <tr>
          <th scope="col">Показатель</th>
          <th scope="col">Было</th>
          <th scope="col" aria-label="Стало" />
          <th scope="col">Стало</th>
        </tr>
      </thead>
      <tbody>
        {shown.map((effect) => (
          <tr key={effect.key} className={effect.improved === true ? 'effects--better' : effect.improved === false ? 'effects--worse' : ''}>
            <th scope="row">{effect.label}</th>
            <td>{formatValue(effect.before, effect.unit)}</td>
            <td aria-hidden="true">
              <ArrowRight size={13} />
            </td>
            <td>
              {formatValue(effect.after, effect.unit)}
              {effect.improved === true && <span className="visually-hidden"> — лучше</span>}
              {effect.improved === false && <span className="visually-hidden"> — хуже</span>}
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function formatValue(value: number | null, unit: string): string {
  if (value === null || value === undefined) return '—';
  return `${formatNumber(value)}${unit ? ` ${unit}` : ''}`;
}

function CapacityBlock({ capacity }: { capacity: CapacityReport }) {
  if (!capacity.available) {
    return (
      <section className="assistant__section">
        <header className="assistant__section-head">
          <h3>Пропускная способность</h3>
        </header>
        <p className="assistant__hint">{capacity.reason ?? 'Недостаточно данных о потоке посетителей.'}</p>
      </section>
    );
  }
  return (
    <section className="assistant__section">
      <header className="assistant__section-head">
        <h3>
          <Users size={15} aria-hidden="true" /> Час пик
        </h3>
        <span>{formatNumber(capacity.peak_arrivals_per_hour, 1)} посетителей в час</span>
      </header>
      <table className="capacity">
        <thead>
          <tr>
            <th scope="col">Группа окон</th>
            <th scope="col">Окон</th>
            <th scope="col">Загрузка</th>
            <th scope="col">Ожидание</th>
          </tr>
        </thead>
        <tbody>
          {capacity.groups.map((group) => (
            <tr key={group.name}>
              <th scope="row">{group.name}</th>
              <td>
                {group.windows.length}
                {group.windows_needed > group.windows.length && <span className="capacity__need"> нужно {group.windows_needed}</span>}
              </td>
              <td className={group.utilization >= 0.85 ? 'capacity--hot' : ''}>{Math.round(group.utilization * 100)}%</td>
              <td>{group.stable ? `${formatNumber(group.mean_wait_min, 1)} мин` : 'растёт без предела'}</td>
            </tr>
          ))}
        </tbody>
      </table>
      {capacity.waiting_capacity_persons !== null && capacity.waiting_capacity_persons !== undefined && (
        <p className="assistant__hint">
          Мест ожидания: {capacity.waiting_capacity_persons}, очередь в пик: {capacity.peak_queue_persons ?? '—'}
          {capacity.fits_waiting_area === false ? ' — не помещается в зону ожидания.' : '.'}
        </p>
      )}
      {capacity.assumptions.length > 0 && (
        <details className="rec__assumptions">
          <summary>
            Допущения расчёта: {capacity.assumptions.length}
            <ChevronDown size={14} aria-hidden="true" />
          </summary>
          <ul>
            {capacity.assumptions.map((text, index) => (
              <li key={index}>{text}</li>
            ))}
          </ul>
        </details>
      )}
    </section>
  );
}

/** Шаги улучшения сводятся к итоговым изменениям: для каждого объекта берётся последнее место. */
