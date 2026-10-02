/**
 * Сроки осмотра в «Эксплуатации»: что просрочено и что подходит в ближайший месяц. Щелчок по
 * строке показывает объект на плане и открывает его паспорт.
 */

import { CalendarPlus, QrCode } from 'lucide-react';
import { buildCalendar, downloadCalendar, inspectionEvents } from '../lib/ics';
import { Plate } from '../components/Plate';
import { hashOf } from '../lib/router';
import type { AddressBook } from '../lib/addresses';
import { key } from '../lib/addresses';
import { inspectionState, SOON_DAYS } from '../lib/inspections';
import type { EquipmentTypeInfo } from '../lib/plan';
import { useWorkspace } from '../state/store';

interface MaintenanceProps {
  book: AddressBook;
  types: EquipmentTypeInfo[];
  onPick: (id: string) => void;
}

export function Maintenance({ book, types, onPick }: MaintenanceProps) {
  const doc = useWorkspace((s) => s.doc);
  const selection = useWorkspace((s) => s.selection);
  const planId = useWorkspace((s) => s.planId);
  const equipment = doc?.equipment ?? [];
  const rows = equipment
    .map((item) => ({ item, state: inspectionState(item.inventory?.next_inspection) }))
    .filter(({ state }) => state.tone === 'overdue' || state.tone === 'soon')
    .sort((a, b) => (a.state.days ?? 0) - (b.state.days ?? 0));
  const overdue = rows.filter(({ state }) => state.tone === 'overdue').length;
  const soon = rows.length - overdue;
  const dated = equipment.filter((item) => item.inventory?.next_inspection).length;
  const label = (type: string) => types.find((t) => t.code === type)?.label ?? type;

  return (
    <section className="maintenance" aria-labelledby="maintenance-title">
      <h2 id="maintenance-title" className="maintenance__title">
        Сроки осмотра
      </h2>
      {rows.length > 0 ? (
        <p className="maintenance__summary">
          {overdue > 0 && (
            <span className="maintenance__count maintenance__count--overdue">
              <span className="maintenance__mark" aria-hidden="true" />
              Просрочено: <b>{overdue}</b>
            </span>
          )}
          {soon > 0 && (
            <span className="maintenance__count maintenance__count--soon">
              <span className="maintenance__mark" aria-hidden="true" />
              В ближайшие {SOON_DAYS} дней: <b>{soon}</b>
            </span>
          )}
        </p>
      ) : (
        <p className="maintenance__summary">
          {dated > 0 ? 'Все сроки осмотра в порядке.' : 'Сроки осмотра пока не внесены: их заполняют в паспорте объекта.'}
        </p>
      )}
      {rows.length > 0 && (
        <ul className="maintenance__list">
          {rows.map(({ item, state }) => (
            <li key={item.id}>
              <button
                type="button"
                className={`maintenance__item maintenance__item--${state.tone}`}
                aria-current={selection?.kind === 'equipment' && selection.id === item.id ? 'true' : undefined}
                onClick={() => onPick(item.id)}
              >
                <span className="maintenance__mark" aria-hidden="true" />
                <span className="maintenance__name">
                  {item.label || label(item.type)}
                  <span className="maintenance__when">{state.relative}</span>
                </span>
                <Plate surface="steel" size="sm">
                  {book.byId.get(key('equipment', item.id)) ?? ''}
                </Plate>
              </button>
            </li>
          ))}
        </ul>
      )}
      <p className="maintenance__hint">Паспорт любого объекта — по щелчку на плане.</p>
      {planId && (
        <a className="maintenance__tags" href={hashOf({ name: 'tags', id: planId })}>
          <QrCode size={15} aria-hidden="true" /> QR-бирки для печати
        </a>
      )}
      {dated > 0 && (
        <button
          type="button"
          className="maintenance__tags maintenance__calendar"
          onClick={() => {
            if (!doc) return;
            const events = inspectionEvents(doc, planId ?? doc.ops.id, (id) => book.byId.get(key('equipment', id)) ?? '', label);
            downloadCalendar(`Осмотры — ${doc.ops.name ?? doc.ops.id}`, buildCalendar(`Осмотры: ${doc.ops.name ?? doc.ops.id}`, events));
          }}
        >
          <CalendarPlus size={15} aria-hidden="true" /> Сроки осмотров в календарь (.ics)
        </button>
      )}
    </section>
  );
}
