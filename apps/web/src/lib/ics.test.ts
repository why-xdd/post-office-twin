import { describe, expect, it } from 'vitest';
import { buildCalendar, escapeText, fold } from './ics';

describe('ics', () => {
  it('escapes special characters', () => {
    expect(escapeText('a, b; c\\d\nе')).toBe('a\\, b\; c\\\\d\\nе');
  });

  it('folds long lines by bytes, not characters', () => {
    const line = 'SUMMARY:' + 'ж'.repeat(60);
    const folded = fold(line);
    for (const part of folded.split('\r\n')) expect(new TextEncoder().encode(part).length).toBeLessThanOrEqual(75);
    expect(folded.replace(/\r\n /g, '')).toBe(line);
  });

  it('makes an all-day event ending the next day with a reminder', () => {
    const text = buildCalendar('Осмотры', [{ uid: 'a@b', date: '2026-12-31', title: 'Осмотр: окно', description: 'x' }], new Date('2026-10-02T10:00:00Z'));
    expect(text).toContain('DTSTART;VALUE=DATE:20261231');
    expect(text).toContain('DTEND;VALUE=DATE:20270101');
    expect(text).toContain('TRIGGER:-P1D');
    expect(text.endsWith('END:VCALENDAR\r\n')).toBe(true);
  });
});
