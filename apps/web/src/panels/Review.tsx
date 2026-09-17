/**
 * Обзор плана: что нарушено — простыми словами, и одна кнопка, чтобы исправить.
 *
 * Номера в списке совпадают с номерами меток на плане. Полный текст нарушения и пункт нормы
 * раскрываются по щелчку; инструменты редактора сюда не выносятся.
 */

import { ArrowRight, Check, ChevronDown, CircleCheck, LoaderCircle, Save, Undo2, Wand2 } from 'lucide-react';
import { useEffect, useMemo, useRef, useState } from 'react';
import type { AddressBook } from '../lib/addresses';
import { ApiError } from '../lib/api';
import { proposeFix, type FixProposal, type Shift } from '../lib/fixProposal';
import { humanize } from '../lib/humanize';
import type { Finding } from '../lib/plan';
import { objectName, plainFinding } from '../lib/plainFindings';
import { formatNumber, plural } from '../lib/planFacts';
import { useWorkspace } from '../state/store';
import './review.css';

interface ReviewProps {
  book: AddressBook;
  saving: boolean;
  onSave: () => void;
  onFocusObject: (id: string) => void;
  onNotice: (text: string) => void;
}

type FixState =
  | { kind: 'idle' }
  | { kind: 'waiting' }
  | { kind: 'shown'; proposal: FixProposal }
  | { kind: 'failed'; message: string };

/** Пока план не менялся, расчёт исправления не запускается повторно при каждой проверке. */
const PRECOMPUTE_DELAY_MS = 600;

export function Review({ book, saving, onSave, onFocusObject, onNotice }: ReviewProps) {
  const doc = useWorkspace((s) => s.doc);
  const report = useWorkspace((s) => s.report);
  const reportState = useWorkspace((s) => s.reportState);
  const active = useWorkspace((s) => s.activeFinding);
  const setActive = useWorkspace((s) => s.setActiveFinding);
  const highlight = useWorkspace((s) => s.highlight);
  const setPreview = useWorkspace((s) => s.setPreview);
  const applyChanges = useWorkspace((s) => s.applyChanges);
  const latch = useWorkspace((s) => s.latch);
  const undo = useWorkspace((s) => s.undo);
  const dirty = useWorkspace((s) => s.doc !== null && JSON.stringify(s.doc) !== s.savedSnapshot);

  const [fix, setFix] = useState<FixState>({ kind: 'idle' });
  const [fixed, setFixed] = useState<{ before: number } | null>(null);
  const [ready, setReady] = useState<FixProposal | null>(null);
  const requested = useRef(false);
  const listRef = useRef<HTMLOListElement>(null);

  const errors = useMemo(() => (report?.findings ?? []).filter((f) => f.severity === 'error'), [report]);
  const notes = useMemo(() => (report?.findings ?? []).filter((f) => f.severity !== 'error'), [report]);

  // Расчёт исправления заранее, как только видно, что исправлять есть что.
  useEffect(() => {
    setReady(null);
    if (!doc || !report || reportState !== 'ready' || report.errors === 0) return;
    let cancelled = false;
    const timer = window.setTimeout(() => {
      proposeFix(doc)
        .then((proposal) => {
          if (cancelled) return;
          setReady(proposal);
          if (requested.current) {
            requested.current = false;
            show(proposal);
          }
        })
        .catch((error: unknown) => {
          if (cancelled || !requested.current) return;
          requested.current = false;
          setFix({
            kind: 'failed',
            message: error instanceof ApiError ? error.message : 'Сервер не ответил. Попробуйте ещё раз.',
          });
        });
    }, PRECOMPUTE_DELAY_MS);
    return () => {
      cancelled = true;
      window.clearTimeout(timer);
    };
    // show стабилен по смыслу: он только меняет состояние панели.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [doc, report, reportState]);

  // Уходя из обзора, предпросмотр не оставляем на плане.
  useEffect(() => () => setPreview(null), [setPreview]);

  // Метка, выбранная на плане, раскрывает свой пункт в списке.
  useEffect(() => {
    if (active === null) return;
    listRef.current?.querySelector(`[data-index="${active}"]`)?.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
  }, [active]);

  const show = (proposal: FixProposal) => {
    setActive(null);
    highlight([]);
    setPreview(proposal.changes);
    setFix({ kind: 'shown', proposal });
  };

  const requestFix = () => {
    if (ready) {
      show(ready);
      return;
    }
    requested.current = true;
    setFix({ kind: 'waiting' });
    if (doc) {
      // Если заранее посчитать не успели, запрос уже в пути или стартует здесь же — кэш общий.
      proposeFix(doc)
        .then((proposal) => {
          if (!requested.current) return;
          requested.current = false;
          show(proposal);
        })
        .catch((error: unknown) => {
          requested.current = false;
          setFix({
            kind: 'failed',
            message: error instanceof ApiError ? error.message : 'Сервер не ответил. Попробуйте ещё раз.',
          });
        });
    }
  };

  const cancelFix = () => {
    requested.current = false;
    setPreview(null);
    setFix({ kind: 'idle' });
  };

  const applyFix = (proposal: FixProposal) => {
    applyChanges(proposal.changes);
    latch(proposal.movedObjects);
    setPreview(null);
    setFix({ kind: 'idle' });
    setFixed({ before: proposal.errorsBefore });
    onNotice('Объекты переставлены. Отменить — Ctrl+Z');
  };

  const toggle = (index: number, finding: Finding) => {
    if (fix.kind === 'shown') cancelFix();
    if (active === index) {
      setActive(null);
      highlight([]);
      return;
    }
    setActive(index);
    highlight(finding.objects);
    const first = finding.objects.find((id) => doc?.equipment?.some((e) => e.id === id) || doc?.zones?.some((z) => z.id === id));
    if (first) onFocusObject(first);
  };

  if (!report) {
    return (
      <div className="review review--pending">
        {reportState === 'error' ? (
          <p>Не удалось проверить план: сервер не ответил. Проверка повторится при следующем изменении.</p>
        ) : (
          <p className="review__loading">
            <LoaderCircle size={18} className="spin" aria-hidden="true" />
            Проверяем план по нормам
          </p>
        )}
      </div>
    );
  }

  const clean = report.errors === 0;

  return (
    <div className="review">
      <header className={`review__head${clean ? ' review__head--clean' : ''}`} aria-live="polite">
        {clean ? (
          <>
            <CircleCheck size={28} aria-hidden="true" className="review__head-icon" />
            <div>
              <h2>Нарушений нет</h2>
              <p>
                {fixed
                  ? `Исправлено ${fixed.before} ${plural(fixed.before, 'нарушение', 'нарушения', 'нарушений')}. План соответствует нормам.`
                  : `План проверен по ${report.checked_rules.length} ${plural(report.checked_rules.length, 'правилу', 'правилам', 'правилам')} — всё в порядке.`}
              </p>
            </div>
          </>
        ) : (
          <div>
            <h2>
              <span className="review__count">{report.errors}</span>{' '}
              {plural(report.errors, 'нарушение', 'нарушения', 'нарушений')} норм
            </h2>
            <p>Номера в списке совпадают с метками на плане. Нажмите на пункт, чтобы увидеть подробности.</p>
          </div>
        )}
        {reportState === 'checking' && <LoaderCircle size={16} className="spin review__rechecking" aria-label="Перепроверка" />}
      </header>

      {clean && (fixed || dirty) && (
        <div className="review__done">
          <button type="button" className="btn btn--label" onClick={onSave} disabled={saving || !dirty}>
            {saving ? <LoaderCircle size={16} className="spin" aria-hidden="true" /> : <Save size={16} aria-hidden="true" />}
            {dirty ? 'Сохранить версию' : 'Сохранено'}
          </button>
          {fixed && dirty && (
            <button
              type="button"
              className="btn btn--outline"
              onClick={() => {
                undo();
                setFixed(null);
              }}
            >
              <Undo2 size={16} aria-hidden="true" />
              Вернуть как было
            </button>
          )}
        </div>
      )}

      {errors.length > 0 && (
        <ol className="review__list" ref={listRef}>
          {errors.map((finding) => {
            const index = report.findings.indexOf(finding);
            return (
              <FindingItem
                key={index}
                number={errors.indexOf(finding) + 1}
                index={index}
                finding={finding}
                open={active === index}
                book={book}
                onToggle={() => toggle(index, finding)}
              />
            );
          })}
        </ol>
      )}

      {notes.length > 0 && (
        <details className="review__notes">
          <summary>
            {clean ? '' : 'Ещё '}
            {notes.length} {plural(notes.length, 'замечание', 'замечания', 'замечаний')} — не критично
            <ChevronDown size={15} aria-hidden="true" />
          </summary>
          <ol className="review__list review__list--notes">
            {notes.map((finding) => {
              const index = report.findings.indexOf(finding);
              return (
                <FindingItem
                  key={index}
                  index={index}
                  finding={finding}
                  open={active === index}
                  book={book}
                  onToggle={() => toggle(index, finding)}
                />
              );
            })}
          </ol>
        </details>
      )}

      {!clean && (
        <section className={`review__fix${fix.kind === 'shown' ? ' review__fix--shown' : ''}`} aria-live="polite">
          {fix.kind === 'shown' ? (
            <ProposalCard proposal={fix.proposal} onApply={() => applyFix(fix.proposal)} onCancel={cancelFix} />
          ) : (
            <>
              <button type="button" className="btn btn--label review__fix-button" onClick={requestFix} disabled={fix.kind === 'waiting'}>
                {fix.kind === 'waiting' ? <LoaderCircle size={18} className="spin" aria-hidden="true" /> : <Wand2 size={18} aria-hidden="true" />}
                {fix.kind === 'waiting' ? 'Подбираем расстановку…' : 'Исправить автоматически'}
              </button>
              <p className="review__fix-note">
                {fix.kind === 'failed'
                  ? fix.message
                  : 'Покажем на плане, какие объекты куда переставить. План изменится, только если вы согласитесь.'}
              </p>
            </>
          )}
        </section>
      )}
    </div>
  );
}

function FindingItem({
  number,
  index,
  finding,
  open,
  book,
  onToggle,
}: {
  number?: number;
  index: number;
  finding: Finding;
  open: boolean;
  book: AddressBook;
  onToggle: () => void;
}) {
  const doc = useWorkspace((s) => s.doc);
  const plain = plainFinding(finding, doc);
  const id = `finding-${index}`;
  return (
    <li className={`review-item review-item--${finding.severity}${open ? ' review-item--open' : ''}`} data-index={index}>
      <button type="button" className="review-item__row" aria-expanded={open} aria-controls={id} onClick={onToggle}>
        {number !== undefined ? (
          <span className="review-item__pin" aria-label={`Метка ${number}`}>
            {number}
          </span>
        ) : (
          <span className="review-item__dot" aria-hidden="true" />
        )}
        <span className="review-item__text">
          <span className="review-item__title">{plain.title}</span>
          {plain.measure && <span className="review-item__measure">{plain.measure}</span>}
          {plain.culprits && <span className="review-item__culprits">{plain.culprits}</span>}
        </span>
        <ChevronDown size={16} aria-hidden="true" className="review-item__chevron" />
      </button>
      {open && (
        <div className="review-item__more" id={id}>
          {plain.impact && <p className="review-item__impact">{plain.impact}</p>}
          <p className="review-item__message">{humanize(finding.message, doc, book)}</p>
          <cite>
            {finding.source}
            {finding.clause ? `, ${finding.clause}` : ''}
          </cite>
        </div>
      )}
    </li>
  );
}

function ProposalCard({ proposal, onApply, onCancel }: { proposal: FixProposal; onApply: () => void; onCancel: () => void }) {
  const doc = useWorkspace((s) => s.doc);
  const names = proposal.movedObjects.map((id) => objectName(doc, id) ?? id);
  const solved = proposal.errorsBefore - proposal.errorsAfter;

  if (proposal.changes.length === 0 || solved <= 0) {
    return (
      <div className="proposal">
        <p className="proposal__title">Автоматически исправить не получилось</p>
        <p className="review__fix-note">
          Перестановкой объектов нарушения не снимаются. Откройте редактор: там ассистент предложит варианты по
          каждому нарушению.
        </p>
        <button type="button" className="btn btn--outline" onClick={onCancel}>
          Понятно
        </button>
      </div>
    );
  }

  return (
    <div className="proposal">
      <p className="proposal__title">
        Переставить {names.length} {plural(names.length, 'объект', 'объекта', 'объектов')}
      </p>
      <p className="proposal__hint">Пунктирные стрелки на плане показывают, куда переедет каждый объект.</p>
      <dl className="proposal__shifts">
        <ShiftRow label="Нарушений" shift={{ before: proposal.errorsBefore, after: proposal.errorsAfter }} better="down" />
        {proposal.entrance && <ShiftRow label="Проход от входа" shift={proposal.entrance} unit="м" better="up" />}
        {proposal.waiting && <ShiftRow label="Мест в зоне ожидания" shift={proposal.waiting} better="up" />}
      </dl>
      <p className="proposal__objects">{names.join(', ')}</p>
      <div className="proposal__actions">
        <button type="button" className="btn btn--brass" onClick={onApply}>
          <Check size={17} aria-hidden="true" />
          Применить
        </button>
        <button type="button" className="btn btn--outline" onClick={onCancel}>
          Отмена
        </button>
      </div>
    </div>
  );
}

function ShiftRow({ label, shift, unit, better }: { label: string; shift: Shift; unit?: string; better: 'up' | 'down' }) {
  const improved = better === 'up' ? shift.after > shift.before : shift.after < shift.before;
  const digits = unit ? 2 : 0;
  return (
    <div className={`shift${improved ? ' shift--better' : ' shift--worse'}`}>
      <dt>{label}</dt>
      <dd>
        <span className="shift__before">{formatNumber(shift.before, digits)}</span>
        <ArrowRight size={14} aria-hidden="true" />
        <b>
          {formatNumber(shift.after, digits)}
          {unit ? ` ${unit}` : ''}
        </b>
      </dd>
    </div>
  );
}
