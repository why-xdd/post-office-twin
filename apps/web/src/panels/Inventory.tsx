/**
 * Инвентаризация: перечень размещённого оборудования по зонам и типам. Перечень строится
 * из текущего плана, поэтому любая перестановка сразу отражается в учёте.
 */

import { Download, FileCheck2, Upload, X } from 'lucide-react';
import { useMemo, useRef, useState } from 'react';
import { api, ApiError } from '../lib/api';
import { Plate } from '../components/Plate';
import type { AddressBook } from '../lib/addresses';
import { key } from '../lib/addresses';
import type { EquipmentTypeInfo, Reconciliation } from '../lib/plan';
import { formatNumber, planArea, plural, polygonArea } from '../lib/planFacts';
import { useWorkspace } from '../state/store';
import { glyphFor } from '../canvas/glyphs';
import './inventory.css';

interface ExchangeState {
  busy: 'export' | 'reconcile' | null;
  error: string | null;
  result: Reconciliation | null;
  checked: unknown;
}

// Результат сверки переживает переключение вкладок: щелчок по строке открывает паспорт объекта,
// а вернувшись к перечню, человек должен увидеть ту же сверку.
const lastExchange = new Map<string, ExchangeState>();

interface InventoryProps {
  book: AddressBook;
  types: Map<string, EquipmentTypeInfo>;
  onFocusObject: (id: string) => void;
}

export function Inventory({ book, types, onFocusObject }: InventoryProps) {
  const doc = useWorkspace((s) => s.doc);
  const select = useWorkspace((s) => s.select);
  const selection = useWorkspace((s) => s.selection);
  const fileInput = useRef<HTMLInputElement>(null);
  const planKey = doc?.ops.id ?? '';
  const [exchange, setExchangeState] = useState<ExchangeState>(
    () => lastExchange.get(planKey) ?? { busy: null, error: null, result: null, checked: null },
  );
  const setExchange = (next: ExchangeState | ((state: ExchangeState) => ExchangeState)) =>
    setExchangeState((state) => {
      const value = typeof next === 'function' ? next(state) : next;
      lastExchange.set(planKey, { ...value, busy: null });
      return value;
    });

  const groups = useMemo(() => {
    if (!doc) return [];
    const zones = new Map((doc.zones ?? []).map((z) => [z.id, z]));
    const byZone = new Map<string, typeof doc.equipment>();
    for (const item of doc.equipment ?? []) {
      const zoneId = item.zone && zones.has(item.zone) ? item.zone : '';
      byZone.set(zoneId, [...(byZone.get(zoneId) ?? []), item]);
    }
    return [...byZone.entries()]
      .map(([zoneId, items]) => ({ zone: zones.get(zoneId) ?? null, items: items ?? [] }))
      .sort((a, b) => (book.byId.get(key('zone', a.zone?.id ?? '')) ?? 'Я').localeCompare(book.byId.get(key('zone', b.zone?.id ?? '')) ?? 'Я'));
  }, [doc, book]);

  const byType = useMemo(() => {
    const counts = new Map<string, number>();
    for (const item of doc?.equipment ?? []) counts.set(item.type, (counts.get(item.type) ?? 0) + 1);
    return [...counts.entries()].sort((a, b) => b[1] - a[1]);
  }, [doc]);

  if (!doc) return null;

  const exportCsv = () => {
    const rows = [['Адрес', 'Идентификатор', 'Подпись', 'Тип', 'Зона', 'X, м', 'Y, м', 'Длина X, м', 'Длина Y, м', 'Поворот, °', 'Серийный номер', 'Следующий осмотр']];
    for (const item of doc.equipment ?? []) {
      const zone = doc.zones?.find((z) => z.id === item.zone);
      rows.push([
        book.byId.get(key('equipment', item.id)) ?? '',
        item.id,
        item.label ?? '',
        types.get(item.type)?.label ?? item.type,
        zone?.label ?? '',
        String(item.position[0]).replace('.', ','),
        String(item.position[1]).replace('.', ','),
        String(item.size_m[0]).replace('.', ','),
        String(item.size_m[1]).replace('.', ','),
        String(item.rotation_deg ?? 0),
        item.inventory?.serial_number ?? '',
        item.inventory?.next_inspection ?? '',
      ]);
    }
    const csv = rows.map((row) => row.map((cell) => `"${cell.replaceAll('"', '""')}"`).join(';')).join('\r\n');
    const blob = new Blob(['\uFEFF' + csv], { type: 'text/csv;charset=utf-8' });
    const url = URL.createObjectURL(blob);
    const link = document.createElement('a');
    link.href = url;
    link.download = `${(doc.ops.name ?? 'план').replace(/[\\/:*?"<>|]/g, '_')} — оборудование.csv`;
    link.click();
    URL.revokeObjectURL(url);
  };

  const total = doc.equipment?.length ?? 0;

  const exportForAccounting = async () => {
    setExchange((s) => ({ ...s, busy: 'export', error: null }));
    try {
      const { blob, filename } = await api.exportForAccounting(doc);
      const url = URL.createObjectURL(blob);
      const link = document.createElement('a');
      link.href = url;
      link.download = filename;
      link.click();
      URL.revokeObjectURL(url);
      setExchange((s) => ({ ...s, busy: null }));
    } catch (error) {
      setExchange((s) => ({ ...s, busy: null, error: error instanceof ApiError ? error.message : 'Не удалось выгрузить опись' }));
    }
  };

  const reconcile = async (file: File) => {
    setExchange({ busy: 'reconcile', error: null, result: null, checked: null });
    try {
      const result = await api.reconcile(doc, file);
      setExchange({ busy: null, error: null, result, checked: doc });
    } catch (error) {
      setExchange({ busy: null, error: error instanceof ApiError ? error.message : 'Не удалось сверить с учётом', result: null, checked: null });
    }
  };

  const focus = (id: string) => {
    select({ kind: 'equipment', id });
    onFocusObject(id);
  };

  const result = exchange.result;
  const moved = result?.matched.filter((m) => m.location_differs) ?? [];
  const serial = result?.matched.filter((m) => m.serial_differs) ?? [];
  const stale = result !== null && exchange.checked !== doc;
  const clean = result !== null && !result.missing_on_plan.length && !result.not_in_accounting.length && !moved.length && !serial.length;

  return (
    <div className="inventory">
      <header className="inventory__head">
        <div>
          <h2>Перечень оборудования</h2>
          <p>
            {total} {plural(total, 'объект', 'объекта', 'объектов')} на {formatNumber(planArea(doc))} м²
          </p>
        </div>
        <button type="button" className="btn btn--outline" onClick={exportCsv} disabled={total === 0}>
          <Download size={15} aria-hidden="true" />
          CSV
        </button>
      </header>

      <section className="ledger" aria-labelledby="ledger-title">
        <div className="ledger__head">
          <h3 id="ledger-title">Учёт в 1С</h3>
          <p>Выгрузите опись для загрузки в 1С или сверьте план с ведомостью основных средств (CSV).</p>
        </div>
        <div className="ledger__actions">
          <button type="button" className="btn btn--outline" onClick={exportForAccounting} disabled={total === 0 || exchange.busy !== null}>
            <Download size={15} aria-hidden="true" />
            {exchange.busy === 'export' ? 'Готовим опись…' : 'Опись для 1С'}
          </button>
          <button type="button" className="btn btn--outline" onClick={() => fileInput.current?.click()} disabled={exchange.busy !== null}>
            <Upload size={15} aria-hidden="true" />
            {exchange.busy === 'reconcile' ? 'Сверяем…' : 'Сверить с ведомостью'}
          </button>
          <input
            ref={fileInput}
            type="file"
            accept=".csv,.txt,text/csv"
            hidden
            onChange={(event) => {
              const file = event.target.files?.[0];
              event.target.value = '';
              if (file) void reconcile(file);
            }}
          />
        </div>
        {exchange.error && (
          <p className="ledger__error" role="alert">
            {exchange.error}
          </p>
        )}
        <div aria-live="polite">
          {result && (
            <div className="ledger__result">
              {stale && <p className="ledger__stale">План изменён после сверки — загрузите ведомость ещё раз, чтобы сверить текущий вариант.</p>}
              <div className={`ledger__summary${clean ? ' ledger__summary--clean' : ''}`}>
                <FileCheck2 size={16} aria-hidden="true" />
                <p>{result.summary}</p>
                <button type="button" className="ledger__close" onClick={() => setExchange((s) => ({ ...s, result: null }))} aria-label="Скрыть результат сверки">
                  <X size={14} aria-hidden="true" />
                </button>
              </div>

              {result.missing_on_plan.length > 0 && (
                <div className="ledger__group">
                  <h4>Числится, но нет на плане · {result.missing_on_plan.length}</h4>
                  <ul>
                    {result.missing_on_plan.map((row) => (
                      <li key={`m${row.line}`} className="ledger__row ledger__row--static">
                        <span className="ledger__number">{row.inventory_number ?? row.serial_number ?? row.plan_id}</span>
                        <span>{row.name ?? 'без наименования'}</span>
                        <span className="ledger__note">{row.location ?? `строка ${row.line}`}</span>
                      </li>
                    ))}
                  </ul>
                </div>
              )}

              {result.not_in_accounting.length > 0 && (
                <div className="ledger__group">
                  <h4>Стоит на плане, но не числится · {result.not_in_accounting.length}</h4>
                  <ul>
                    {result.not_in_accounting.map((item) => (
                      <li key={`s${item.object_id}`}>
                        <button type="button" className="ledger__row" onClick={() => focus(item.object_id)}>
                          <span className="ledger__number">{item.inventory_number ?? item.serial_number ?? '—'}</span>
                          <span>{item.label}</span>
                          <span className="ledger__note">{item.zone ?? 'вне зон'}</span>
                        </button>
                      </li>
                    ))}
                  </ul>
                </div>
              )}

              {moved.length > 0 && (
                <div className="ledger__group">
                  <h4>Стоит не там, где числится · {moved.length}</h4>
                  <ul>
                    {moved.map((m) => (
                      <li key={`l${m.object_id}`}>
                        <button type="button" className="ledger__row" onClick={() => focus(m.object_id)}>
                          <span className="ledger__number">{m.inventory_number ?? '—'}</span>
                          <span>{m.label}</span>
                          <span className="ledger__note">
                            на плане: {m.zone ?? 'вне зон'}, в учёте: {m.accounting_location}
                          </span>
                        </button>
                      </li>
                    ))}
                  </ul>
                </div>
              )}

              {serial.length > 0 && (
                <div className="ledger__group">
                  <h4>Расходится серийный номер · {serial.length}</h4>
                  <ul>
                    {serial.map((m) => (
                      <li key={`n${m.object_id}`}>
                        <button type="button" className="ledger__row" onClick={() => focus(m.object_id)}>
                          <span className="ledger__number">{m.inventory_number ?? '—'}</span>
                          <span>{m.label}</span>
                          <span className="ledger__note">проверьте паспорт объекта</span>
                        </button>
                      </li>
                    ))}
                  </ul>
                </div>
              )}
            </div>
          )}
        </div>
      </section>

      {byType.length > 0 && (
        <table className="inventory__types">
          <caption>По типам</caption>
          <tbody>
            {byType.map(([type, count]) => {
              const Glyph = glyphFor(type);
              return (
                <tr key={type}>
                  <th scope="row">
                    <Glyph size={14} aria-hidden="true" />
                    {types.get(type)?.label ?? type}
                  </th>
                  <td>{count}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      )}

      {total === 0 && <p className="inventory__empty">На плане пока нет оборудования. Выберите объект в библиотеке слева и щёлкните по плану.</p>}

      {groups.map(({ zone, items }) => (
        <section key={zone?.id ?? 'none'} className="inventory__zone">
          <h3>
            {zone ? (
              <>
                <Plate surface="label" size="sm">
                  {book.byId.get(key('zone', zone.id)) ?? ''}
                </Plate>
                {zone.label ?? zone.type}
                <span>{formatNumber(polygonArea(zone.polygon))} м²</span>
              </>
            ) : (
              'Вне зон'
            )}
          </h3>
          <ul>
            {items.map((item) => {
              const address = book.byId.get(key('equipment', item.id)) ?? '';
              const active = selection?.kind === 'equipment' && selection.id === item.id;
              return (
                <li key={item.id}>
                  <button
                    type="button"
                    className={`inventory__row${active ? ' inventory__row--active' : ''}`}
                    onClick={() => {
                      select({ kind: 'equipment', id: item.id });
                      onFocusObject(item.id);
                    }}
                  >
                    <span className="inventory__address">{address}</span>
                    <span className="inventory__label">{item.label || types.get(item.type)?.label || item.type}</span>
                    <span className="inventory__size">
                      {formatNumber(item.size_m[0])}×{formatNumber(item.size_m[1])}
                    </span>
                  </button>
                </li>
              );
            })}
          </ul>
        </section>
      ))}
    </div>
  );
}
