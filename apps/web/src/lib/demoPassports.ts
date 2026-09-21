/**
 * Синтетические паспортные данные для демо-плана.
 *
 * В файле заказчика нет сотрудников и учётных данных, а режим эксплуатации без них пуст.
 * Данные придуманы, помечены как синтетические и заменяются реальными при интеграции с
 * учётными системами. Даты осмотров считаются от текущего дня, чтобы в демо были и
 * просроченные, и ближайшие сроки.
 */

import type { Equipment, Plan } from './plan';

export const SYNTHETIC_MARK = 'synthetic';

function shiftDays(days: number): string {
  const date = new Date();
  date.setDate(date.getDate() + days);
  return date.toISOString().slice(0, 10);
}

const STAFF = [
  { full_name: 'Смирнова Анна Викторовна', position: 'Оператор связи', personnel_number: '40211', schedule: '2/2, 08:00–20:00', avg_service_time_min: 3.2 },
  { full_name: 'Корнилов Павел Андреевич', position: 'Оператор связи', personnel_number: '40377', schedule: '5/2, 09:00–18:00', avg_service_time_min: 2.8 },
  { full_name: 'Белова Ирина Сергеевна', position: 'Оператор выдачи посылок', personnel_number: '40462', schedule: '2/2, 10:00–22:00', avg_service_time_min: 1.9 },
  { full_name: 'Тихонов Дмитрий Олегович', position: 'Начальник отделения', personnel_number: '40018', schedule: '5/2, 08:30–17:30' },
];

const INTEGRATIONS: Record<string, Record<string, string>> = {
  cashier_window: { WFM: '', DPA: '', SOM: '', Портал: '' },
  pvz_counter: { WFM: '', SOM: '', Портал: '' },
  back_office_workplace: { WFM: '', Портал: '' },
  self_service_kiosk: { Мониторинг: '', RDP: '', Аналитика: '' },
  atm: { Мониторинг: '' },
};

const INVENTORY_BY_TYPE: Record<string, { vendor: string; model: string; lastDays: number; nextDays: number }> = {
  cashier_window: { vendor: 'Поставщик А', model: 'Операционная касса ОК-2', lastDays: -170, nextDays: 12 },
  pvz_counter: { vendor: 'Поставщик А', model: 'Стойка выдачи СВ-20', lastDays: -95, nextDays: 85 },
  self_service_kiosk: { vendor: 'Поставщик Б', model: 'ТСО-500', lastDays: -210, nextDays: -25 },
  atm: { vendor: 'Банк-партнёр', model: 'Банкомат ATM-8', lastDays: -40, nextDays: 50 },
  safe: { vendor: 'Поставщик В', model: 'Сейф С-90', lastDays: -300, nextDays: 65 },
  storage_rack: { vendor: 'Поставщик Г', model: 'СТ-2000', lastDays: -120, nextDays: 245 },
  back_office_workplace: { vendor: 'Поставщик А', model: 'АРМ-П', lastDays: -60, nextDays: 120 },
  info_stand: { vendor: 'Поставщик Д', model: 'Стенд А1', lastDays: -30, nextDays: 335 },
};

export function withDemoPassports(plan: Plan): Plan {
  const copy: Plan = structuredClone(plan);
  let staffIndex = 0;
  let serial = 118_400;
  const equipment: Equipment[] = (copy.equipment ?? []).map((item) => {
    const next: Equipment = { ...item };
    const inventory = INVENTORY_BY_TYPE[item.type];
    if (inventory) {
      serial += 7;
      next.inventory = {
        serial_number: `${item.type === 'atm' ? 'ATM' : 'ОПС'}-${serial}`,
        vendor: inventory.vendor,
        model: inventory.model,
        last_inspection: shiftDays(inventory.lastDays),
        next_inspection: shiftDays(inventory.nextDays),
      };
    }
    if (item.type === 'cashier_window' || item.type === 'pvz_counter' || item.type === 'back_office_workplace') {
      const pick = item.type === 'back_office_workplace' ? STAFF[3] : STAFF[Math.min(staffIndex++, 2)];
      next.staff = { ...pick };
    }
    if (INTEGRATIONS[item.type]) next.integrations = { ...INTEGRATIONS[item.type] };
    if (item.type === 'self_service_kiosk') {
      next.attributes = { ...(item.attributes ?? {}), status: 'Активен', ip_address: '10.23.11.22', uses_per_month: 2532 };
    }
    next.attributes = { ...(next.attributes ?? {}), data_origin: SYNTHETIC_MARK };
    return next;
  });
  copy.equipment = equipment;
  return copy;
}
