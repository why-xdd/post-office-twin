/**
 * Календарь осмотров в формате iCalendar (RFC 5545): срок каждого осмотра — событие на весь день
 * с напоминанием за сутки. Файл открывается в Outlook, Яндекс и Google Календаре.
 */

import type { Plan } from './plan';

export interface InspectionEvent {
  uid: string;
  date: string;
  title: string;
  description: string;
}

/** Экранирование текста по RFC 5545: обратная косая, точка с запятой, запятая, перевод строки. */
export function escapeText(text: string): string {
  return text.replace(/\\/g, '\\\\').replace(/;/g, '\\;').replace(/,/g, '\\,').replace(/\r?\n/g, '\\n');
}

/** Строки длиннее 75 октетов переносятся: продолжение начинается с пробела. */
export function fold(line: string): string {
  const bytes = new TextEncoder();
  if (bytes.encode(line).length <= 75) return line;
  const parts: string[] = [];
  let current = '';
  for (const char of line) {
    if (bytes.encode(current + char).length > (parts.length === 0 ? 75 : 74)) {
      parts.push(current);
      current = char;
    } else current += char;
  }
  parts.push(current);
  return parts.join('\r\n ');
}

const compact = (date: string) => date.replace(/-/g, '');

function nextDay(date: string): string {
  const day = new Date(`${date}T00:00:00Z`);
  day.setUTCDate(day.getUTCDate() + 1);
  return day.toISOString().slice(0, 10);
}

export function inspectionEvents(plan: Plan, planId: string, addressOf: (id: string) => string, labelOf: (type: string) => string): InspectionEvent[] {
  return (plan.equipment ?? [])
    .filter((item) => item.inventory?.next_inspection && /^\d{4}-\d{2}-\d{2}$/.test(item.inventory.next_inspection))
    .map((item) => {
      const name = item.label || labelOf(item.type);
      const address = addressOf(item.id);
      const details = [
        plan.ops.name,
        address && `Адрес на плане: ${address}`,
        item.inventory?.inventory_number && `Инв. № ${item.inventory.inventory_number}`,
        item.staff?.full_name && `Ответственный: ${item.staff.full_name}`,
      ].filter(Boolean);
      return {
        uid: `${item.id}@${planId}.plan-ops`,
        date: item.inventory!.next_inspection!,
        title: `Осмотр: ${name}${address ? ` (${address})` : ''}`,
        description: details.join('\n'),
      };
    });
}

export function buildCalendar(name: string, events: InspectionEvent[], now: Date = new Date()): string {
  const stamp = now.toISOString().replace(/[-:]/g, '').replace(/\.\d{3}/, '');
  const lines = [
    'BEGIN:VCALENDAR',
    'VERSION:2.0',
    'PRODID:-//Plan OPS//Inspections//RU',
    'CALSCALE:GREGORIAN',
    'METHOD:PUBLISH',
    `X-WR-CALNAME:${escapeText(name)}`,
  ];
  for (const event of events) {
    lines.push(
      'BEGIN:VEVENT',
      `UID:${event.uid}`,
      `DTSTAMP:${stamp}`,
      `DTSTART;VALUE=DATE:${compact(event.date)}`,
      `DTEND;VALUE=DATE:${compact(nextDay(event.date))}`,
      `SUMMARY:${escapeText(event.title)}`,
      `DESCRIPTION:${escapeText(event.description)}`,
      'BEGIN:VALARM',
      'ACTION:DISPLAY',
      `DESCRIPTION:${escapeText(event.title)}`,
      'TRIGGER:-P1D',
      'END:VALARM',
      'END:VEVENT',
    );
  }
  lines.push('END:VCALENDAR');
  return lines.map(fold).join('\r\n') + '\r\n';
}

export function downloadCalendar(name: string, text: string): void {
  const safe = name.replace(/[\\/:*?"<>|]+/g, ' ').trim() || 'calendar';
  const url = URL.createObjectURL(new Blob([text], { type: 'text/calendar;charset=utf-8' }));
  const link = document.createElement('a');
  link.href = url;
  link.download = `${safe}.ics`;
  document.body.append(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(url);
}
