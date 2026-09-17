/**
 * Проверка плана. Каждое нарушение — этикетка с адресом, замером против нормы и пунктом
 * документа; наведение подсвечивает на плане объекты, из-за которых оно возникло.
 */

import { CircleCheck, Info, LoaderCircle, OctagonAlert, TriangleAlert } from 'lucide-react';
import type { AddressBook } from '../lib/addresses';
import { formatAddress, key } from '../lib/addresses';
import type { Finding, Severity } from '../lib/plan';
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

const SEVERITY_LABEL: Record<Severity, string> = {
  error: 'Ошибка',
  warning: 'Предупреждение',
  info: 'Сведения',
};

export function Checks({ book, onFocusObject }: ChecksProps) {
  const report = useWorkspace((s) => s.report);
  const state = useWorkspace((s) => s.reportState);
  const highlight = useWorkspace((s) => s.highlight);
  const select = useWorkspace((s) => s.select);

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

  return (
    <div className={`checks${state === 'checking' ? ' checks--stale' : ''}`}>
      <div className="checks__summary" aria-live="polite">
        <SummaryCount severity="error" count={report.errors} words={['ошибка', 'ошибки', 'ошибок']} />
        <SummaryCount severity="warning" count={report.warnings} words={['предупреждение', 'предупреждения', 'предупреждений']} />
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
        <ol className="checks__list">
          {findings.map(({ finding, index }) => (
            <FindingRow
              key={index}
              address={formatAddress('finding', index)}
              finding={finding}
              book={book}
              onEnter={() => highlight(finding.objects)}
              onLeave={() => highlight([])}
              onObject={(id) => {
                select({ kind: book.byId.has(key('equipment', id)) ? 'equipment' : 'zone', id });
                onFocusObject(id);
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
  address,
  finding,
  book,
  onEnter,
  onLeave,
  onObject,
}: {
  address: string;
  finding: Finding;
  book: AddressBook;
  onEnter: () => void;
  onLeave: () => void;
  onObject: (id: string) => void;
}) {
  const Icon = SEVERITY_ICON[finding.severity];
  const hasMeasure = finding.measured !== null && finding.measured !== undefined && finding.required !== null && finding.required !== undefined;
  return (
    <li
      className={`finding finding--${finding.severity}`}
      onMouseEnter={onEnter}
      onMouseLeave={onLeave}
      onFocus={onEnter}
      onBlur={onLeave}
    >
      <div className="finding__head">
        <Plate surface="label" size="sm">
          {address}
        </Plate>
        <span className="finding__severity">
          <Icon size={14} aria-hidden="true" />
          <span className="visually-hidden">{SEVERITY_LABEL[finding.severity]}: </span>
          {finding.title}
        </span>
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
              const objectAddress = book.byId.get(key('equipment', id)) ?? book.byId.get(key('zone', id)) ?? id;
              return (
                <button key={id} type="button" className="chip" onClick={() => onObject(id)} title={`Показать ${id} на плане`}>
                  {objectAddress}
                </button>
              );
            })}
          </span>
        )}
      </div>
    </li>
  );
}
