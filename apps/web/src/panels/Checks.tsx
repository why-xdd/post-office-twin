/**
 * Проверка плана. Каждое нарушение — этикетка с адресом, замером против нормы и пунктом
 * документа. Наведение показывает на плане объекты, из-за которых оно возникло; щелчок
 * закрепляет подсветку, пока её не снимут повторным щелчком или Esc.
 */

import { CircleCheck, Info, LoaderCircle, OctagonAlert, Pin, TriangleAlert } from 'lucide-react';
import { useEffect, useRef } from 'react';
import type { AddressBook } from '../lib/addresses';
import { formatAddress, key } from '../lib/addresses';
import type { Finding, Severity } from '../lib/plan';
import { isNormFinding } from '../lib/plainFindings';
import { formatNumber, plural } from '../lib/planFacts';
import { humanize } from '../lib/humanize';
import { useWorkspace } from '../state/store';
import { Plate } from '../components/Plate';
import './panels.css';

interface ChecksProps {
  book: AddressBook;
  onFocusObject: (id: string) => void;
}

const SEVERITY_ICON: Record<Severity, typeof OctagonAlert> = {
  error: OctagonAlert,
  warning: TriangleAlert,
  info: Info,
};

/** Слова те же, что в обзоре и у ассистента: нарушение нормы, ошибка в данных, замечание. */
function severityLabel(finding: Finding): string {
  if (finding.severity === 'error') return isNormFinding(finding) ? 'Нарушение' : 'Ошибка в данных';
  return finding.severity === 'warning' ? 'Замечание' : 'Сведения';
}

export function Checks({ book, onFocusObject }: ChecksProps) {
  const report = useWorkspace((s) => s.report);
  const state = useWorkspace((s) => s.reportState);
  const doc = useWorkspace((s) => s.doc);
  const highlight = useWorkspace((s) => s.highlight);
  const active = useWorkspace((s) => s.activeFinding);
  const setActive = useWorkspace((s) => s.setActiveFinding);
  const list = useRef<HTMLOListElement>(null);

  // Нарушение закрепили меткой на плане или в «Свойствах» — его строка должна быть на виду.
  useEffect(() => {
    if (active === null) return;
    const row = list.current?.querySelector<HTMLElement>(`[data-finding="${active}"]`);
    const still = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    row?.scrollIntoView({ block: 'nearest', behavior: still ? 'auto' : 'smooth' });
  }, [active]);

  if (!report) {
    return (
      <div className="panel-state">
        {state === 'error' ? (
          <p>Не удалось проверить план: сервер не ответил. Проверка повторится после следующего изменения.</p>
        ) : (
          <p className="panel-state__loading">
            <LoaderCircle size={16} className="spin" aria-hidden="true" />
            Проверяем план по нормам
          </p>
        )}
      </div>
    );
  }

  const findings = report.findings.map((finding, index) => ({ finding, index }));
  const normErrors = report.findings.filter((f) => f.severity === 'error' && isNormFinding(f)).length;
  const dataErrors = report.findings.filter((f) => f.severity === 'error').length - normErrors;
  const pinned = active === null ? undefined : report.findings[active];
  // Ушли со строки — на плане снова закреплённое нарушение, а не пустота.
  const restore = () => highlight(pinned ? pinned.objects : []);
  const onPlan = (id: string) => Boolean(doc?.equipment?.some((e) => e.id === id) || doc?.zones?.some((z) => z.id === id));
  const touch = window.matchMedia('(pointer: coarse)').matches;

  const toggle = (index: number, finding: Finding) => {
    if (active === index) {
      setActive(null);
      highlight([]);
      return;
    }
    setActive(index);
    highlight(finding.objects);
  };

  return (
    <div className={`checks${state === 'checking' ? ' checks--stale' : ''}`}>
      <div className="checks__summary" aria-live="polite">
        <SummaryCount severity="error" count={normErrors} words={['нарушение норм', 'нарушения норм', 'нарушений норм']} />
        {dataErrors > 0 && (
          <SummaryCount severity="error" count={dataErrors} words={['ошибка в данных', 'ошибки в данных', 'ошибок в данных']} />
        )}
        <SummaryCount severity="warning" count={report.warnings} words={['замечание', 'замечания', 'замечаний']} />
        <SummaryCount severity="info" count={report.infos} words={['сведение', 'сведения', 'сведений']} />
        {state === 'checking' && (
          <span className="checks__rechecking">
            <LoaderCircle size={14} className="spin" aria-hidden="true" />
            Перепроверка
          </span>
        )}
      </div>

      {findings.length === 0 ? (
        <div className="checks__clean">
          <CircleCheck size={22} aria-hidden="true" />
          <div>
            <p className="checks__clean-title">Нарушений нет</p>
            <p>
              План соответствует всем применённым нормам: {report.checked_rules.length}{' '}
              {plural(report.checked_rules.length, 'правило', 'правила', 'правил')} и проверки целостности.
            </p>
          </div>
        </div>
      ) : (
        <ol className="checks__list" ref={list}>
          {findings.map(({ finding, index }) => (
            <FindingRow
              key={index}
              index={index}
              address={formatAddress('finding', index)}
              finding={finding}
              book={book}
              pinned={active === index}
              touch={touch}
              onEnter={() => highlight(finding.objects)}
              onLeave={restore}
              onToggle={() => toggle(index, finding)}
              onObject={(id) => {
                // Объект из списка показывается на плане, а нарушение закрепляется: список
                // остаётся на месте, выделение объекта не меняется.
                setActive(index);
                highlight(finding.objects);
                if (onPlan(id)) onFocusObject(id);
              }}
            />
          ))}
        </ol>
      )}

      {report.skipped_rules.length > 0 && (
        <details className="checks__skipped">
          <summary>
            Не применено: {report.skipped_rules.length} {plural(report.skipped_rules.length, 'правило', 'правила', 'правил')}
          </summary>
          <ul>
            {Object.entries(report.skipped_reasons ?? {}).map(([rule, reason]) => (
              <li key={rule}>{reason}</li>
            ))}
          </ul>
        </details>
      )}
    </div>
  );
}

function SummaryCount({ severity, count, words }: { severity: Severity; count: number; words: [string, string, string] }) {
  const Icon = SEVERITY_ICON[severity];
  return (
    <span className={`checks__count checks__count--${severity}${count === 0 ? ' checks__count--zero' : ''}`}>
      <Icon size={15} aria-hidden="true" />
      <b>{count}</b> {plural(count, ...words)}
    </span>
  );
}

function FindingRow({
  index,
  address,
  finding,
  book,
  pinned,
  touch,
  onEnter,
  onLeave,
  onToggle,
  onObject,
}: {
  index: number;
  address: string;
  finding: Finding;
  book: AddressBook;
  pinned: boolean;
  touch: boolean;
  onEnter: () => void;
  onLeave: () => void;
  onToggle: () => void;
  onObject: (id: string) => void;
}) {
  const Icon = SEVERITY_ICON[finding.severity];
  const hasMeasure = finding.measured !== null && finding.measured !== undefined && finding.required !== null && finding.required !== undefined;
  return (
    <li
      data-finding={index}
      className={`finding finding--${finding.severity}${pinned ? ' finding--pinned' : ''}`}
      onMouseEnter={onEnter}
      onMouseLeave={onLeave}
      onFocus={onEnter}
      onBlur={(event) => {
        // Фокус перешёл между кнопками той же карточки — подсветка остаётся.
        if (!event.currentTarget.contains(event.relatedTarget as Node | null)) onLeave();
      }}
      onClick={(event) => {
        // Щелчок по карточке — то же, что по заголовку; у кнопок объектов своё действие.
        if ((event.target as HTMLElement).closest('button, a')) return;
        onToggle();
      }}
    >
      <div className="finding__head">
        <button type="button" className="finding__toggle" aria-pressed={pinned} onClick={onToggle}>
          <Plate surface="label" size="sm">
            {address}
          </Plate>
          <span className="finding__severity">
            <Icon size={14} aria-hidden="true" />
            <span className="visually-hidden">{severityLabel(finding)}: </span>
            {finding.title}
          </span>
        </button>
      </div>
      <p className="finding__message">{humanize(finding.message, useWorkspace.getState().doc, book)}</p>
      {hasMeasure && (
        <p className="finding__measure">
          <span>
            замер <b>{formatNumber(finding.measured)}</b> {finding.unit}
          </span>
          <span aria-hidden="true">·</span>
          <span>
            норма <b>{formatNumber(finding.required)}</b> {finding.unit}
          </span>
        </p>
      )}
      <div className="finding__foot">
        <cite className="finding__source">
          {finding.source}
          {finding.clause ? `, ${finding.clause}` : ''}
        </cite>
        {finding.objects.length > 0 && (
          <span className="finding__objects">
            {finding.objects.map((id) => {
              const objectAddress =
                book.byId.get(key('equipment', id)) ??
                book.byId.get(key('zone', id)) ??
                book.byId.get(key('wall', id)) ??
                book.byId.get(key('door', id)) ??
                book.byId.get(key('window', id)) ??
                id;
              return (
                <button key={id} type="button" className="chip" onClick={() => onObject(id)} title={`Показать ${objectAddress} на плане`}>
                  {objectAddress}
                </button>
              );
            })}
          </span>
        )}
      </div>
      {pinned && (
        <p className="finding__pinned">
          <Pin size={13} aria-hidden="true" />
          {touch ? 'Закреплено на плане. Снять: нажмите на карточку ещё раз' : 'Закреплено на плане. Снять: Esc или щелчок по карточке'}
        </p>
      )}
    </li>
  );
}
