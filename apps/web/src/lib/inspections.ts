/**
 * Сроки осмотра оборудования: просрочен, скоро или в порядке. Один расчёт на паспорт объекта,
 * список обслуживания в «Эксплуатации» и отметки на плане.
 */

import { plural } from './planFacts';

const DAY = 24 * 60 * 60 * 1000;
/** Сколько дней вперёд срок считается близким. */
export const SOON_DAYS = 30;

export type InspectionTone = 'overdue' | 'soon' | 'ok' | 'none';

export interface InspectionState {
  tone: InspectionTone;
  /** Дата и что с ней: «12 сентября 2026 г. — через 3 дня». */
  text: string;
  /** Только срок относительно сегодня: «через 3 дня», «просрочен на 2 дня». */
  relative: string | null;
  /** Дней до срока; отрицательное — просрочен. */
  days: number | null;
}

export function formatDate(date: string | undefined): string {
  if (!date) return 'Не указан';
  const parsed = new Date(`${date}T00:00:00`);
  if (Number.isNaN(parsed.getTime())) return date;
  return parsed.toLocaleDateString('ru-RU', { day: 'numeric', month: 'long', year: 'numeric' });
}

const dayWord = (days: number) => plural(days, 'день', 'дня', 'дней');

export function inspectionState(date: string | undefined, today: Date = new Date()): InspectionState {
  if (!date) return { tone: 'none', text: 'Не указан', relative: null, days: null };
  const parsed = new Date(`${date}T00:00:00`);
  if (Number.isNaN(parsed.getTime())) return { tone: 'none', text: date, relative: null, days: null };
  const days = Math.round((parsed.getTime() - new Date(today.toDateString()).getTime()) / DAY);
  const formatted = formatDate(date);
  let relative: string | null = null;
  let tone: InspectionTone = 'ok';
  if (days < 0) {
    tone = 'overdue';
    relative = `просрочен на ${-days} ${dayWord(-days)}`;
  } else if (days <= SOON_DAYS) {
    tone = 'soon';
    relative = days === 0 ? 'сегодня' : days === 1 ? 'завтра' : `через ${days} ${dayWord(days)}`;
  }
  return { tone, text: relative ? `${formatted} — ${relative}` : formatted, relative, days };
}
