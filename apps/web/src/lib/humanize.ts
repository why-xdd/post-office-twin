/**
 * Тексты сервера написаны в терминах идентификаторов и десятичных точек. Для человека
 * идентификатор заменяется адресом и подписью объекта, а дробная часть отделяется запятой.
 */

import type { AddressBook } from './addresses';
import { key } from './addresses';
import type { Plan } from './plan';

export function humanize(text: string, plan: Plan | null, book: AddressBook): string {
  const withNames = text.replace(/«([A-Za-z0-9_.-]+)»/g, (whole, id: string) => {
    const item = plan?.equipment?.find((e) => e.id === id);
    if (item) {
      const address = book.byId.get(key('equipment', id));
      return `«${address ? `${address} ` : ''}${item.label ?? id}»`;
    }
    const zone = plan?.zones?.find((z) => z.id === id);
    if (zone) {
      const address = book.byId.get(key('zone', id));
      return `«${address ? `${address} ` : ''}${zone.label ?? id}»`;
    }
    const wall = plan?.walls?.find((w) => w.id === id);
    if (wall) return `«${book.byId.get(key('wall', id)) ?? ''} ${wall.label ?? 'стена'}»`.replace('« ', '«');
    const door = plan?.doors?.find((d) => d.id === id);
    if (door) return `«${book.byId.get(key('door', id)) ?? ''} ${door.label ?? 'дверь'}»`.replace('« ', '«');
    const win = plan?.windows?.find((w) => w.id === id);
    if (win) return `«${book.byId.get(key('window', id)) ?? ''} окно»`.replace('« ', '«');
    return whole;
  });
  // «Окно кассы №1 (eq_cashier_1)» → «Окно кассы №1 (О-01)»
  const withAddresses = withNames.replace(/\(([A-Za-z][A-Za-z0-9_.-]*)\)/g, (whole, id: string) => {
    const address = book.byId.get(key('equipment', id)) ?? book.byId.get(key('zone', id));
    return address ? `(${address})` : whole;
  });
  return withAddresses.replace(/(\d)\.(\d)/g, '$1,$2');
}
