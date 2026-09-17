/**
 * Адреса ячеек.
 *
 * Каждая сущность плана и результата проверки получает короткий адрес — буква раздела и
 * номер ячейки. По адресу к сущности переходят из строки адреса и из любого текста, где он
 * упомянут.
 */

import type { Finding, Plan } from './plan';

export type AddressKind = 'equipment' | 'zone' | 'wall' | 'door' | 'finding' | 'recommendation' | 'rule';

export const ADDRESS_PREFIX: Record<AddressKind, string> = {
  equipment: 'О',
  zone: 'З',
  wall: 'С',
  door: 'Д',
  finding: 'Н',
  recommendation: 'Р',
  rule: 'П',
};

export const ADDRESS_KIND_LABEL: Record<AddressKind, string> = {
  equipment: 'объект',
  zone: 'зона',
  wall: 'стена',
  door: 'проём',
  finding: 'нарушение',
  recommendation: 'рекомендация',
  rule: 'норма',
};

export interface AddressTarget {
  address: string;
  kind: AddressKind;
  id: string;
}

export function formatAddress(kind: AddressKind, index: number): string {
  return `${ADDRESS_PREFIX[kind]}-${String(index + 1).padStart(2, '0')}`;
}

export interface AddressBook {
  byAddress: Map<string, AddressTarget>;
  byId: Map<string, string>;
}

/** Адрес ключуется по «вид:идентификатор», потому что зона и объект могут совпасть по id. */
export function key(kind: AddressKind, id: string): string {
  return `${kind}:${id}`;
}

export function buildAddressBook(
  plan: Plan | null,
  findings: Finding[] = [],
  recommendationIds: string[] = [],
  ruleIds: string[] = [],
): AddressBook {
  const byAddress = new Map<string, AddressTarget>();
  const byId = new Map<string, string>();
  const add = (kind: AddressKind, ids: string[]) => {
    ids.forEach((id, index) => {
      const address = formatAddress(kind, index);
      byAddress.set(address, { address, kind, id });
      byId.set(key(kind, id), address);
    });
  };
  if (plan) {
    add('equipment', (plan.equipment ?? []).map((e) => e.id));
    add('zone', (plan.zones ?? []).map((z) => z.id));
    add('wall', (plan.walls ?? []).map((w) => w.id));
    add('door', (plan.doors ?? []).map((d) => d.id));
  }
  add(
    'finding',
    findings.map((_, index) => String(index)),
  );
  add('recommendation', recommendationIds);
  add('rule', ruleIds);
  return { byAddress, byId };
}

/** Нормализует ввод: латинские двойники букв, пробелы, номер без ведущего нуля. */
export function parseAddress(input: string): string | null {
  const latinToCyrillic: Record<string, string> = { O: 'О', C: 'С', P: 'Р', H: 'Н', '3': 'З' };
  const cleaned = input.trim().toUpperCase().replace(/\s+/g, '');
  const match = cleaned.match(/^([A-ZА-ЯЁ3])-?(\d{1,3})$/);
  if (!match) return null;
  const letter = latinToCyrillic[match[1]] ?? match[1];
  const known = Object.values(ADDRESS_PREFIX);
  if (!known.includes(letter)) return null;
  return `${letter}-${String(Number(match[2])).padStart(2, '0')}`;
}
