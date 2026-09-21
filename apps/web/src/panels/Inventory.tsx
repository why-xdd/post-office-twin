/**
 * Инвентаризация: перечень размещённого оборудования по зонам и типам. Перечень строится
 * из текущего плана, поэтому любая перестановка сразу отражается в учёте.
 */

import { Download } from 'lucide-react';
import { useMemo } from 'react';
import { Plate } from '../components/Plate';
import type { AddressBook } from '../lib/addresses';
import { key } from '../lib/addresses';
import type { EquipmentTypeInfo } from '../lib/plan';
import { formatNumber, planArea, plural, polygonArea } from '../lib/planFacts';
import { useWorkspace } from '../state/store';
import { glyphFor } from '../canvas/glyphs';
import './inventory.css';

interface InventoryProps {
  book: AddressBook;
  types: Map<string, EquipmentTypeInfo>;
  onFocusObject: (id: string) => void;
}

export function Inventory({ book, types, onFocusObject }: InventoryProps) {
  const doc = useWorkspace((s) => s.doc);
  const select = useWorkspace((s) => s.select);
  const selection = useWorkspace((s) => s.selection);

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
    const blob = new Blob(['﻿' + csv], { type: 'text/csv;charset=utf-8' });
    const url = URL.createObjectURL(blob);
    const link = document.createElement('a');
    link.href = url;
    link.download = `${(doc.ops.name ?? 'план').replace(/[\\/:*?"<>|]/g, '_')} — оборудование.csv`;
    link.click();
    URL.revokeObjectURL(url);
  };

  const total = doc.equipment?.length ?? 0;

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
