import { describe, expect, it } from 'vitest';
import { inspectionState } from './inspections';

const today = new Date('2026-09-30T15:00:00');

describe('срок осмотра', () => {
  it('просроченный пишет, на сколько дней, с верным окончанием', () => {
    expect(inspectionState('2026-09-29', today).relative).toBe('просрочен на 1 день');
    expect(inspectionState('2026-09-27', today).relative).toBe('просрочен на 3 дня');
    expect(inspectionState('2026-09-18', today).relative).toBe('просрочен на 12 дней');
    expect(inspectionState('2026-09-29', today).tone).toBe('overdue');
  });

  it('близкий срок называет словами сегодня и завтра', () => {
    expect(inspectionState('2026-09-30', today).relative).toBe('сегодня');
    expect(inspectionState('2026-10-01', today).relative).toBe('завтра');
    expect(inspectionState('2026-10-21', today).relative).toBe('через 21 день');
    expect(inspectionState('2026-10-02', today).tone).toBe('soon');
  });

  it('дальний срок — просто дата, без тревоги', () => {
    const state = inspectionState('2026-12-01', today);
    expect(state.tone).toBe('ok');
    expect(state.relative).toBeNull();
  });

  it('пустая или непонятная дата — «не указан», а не ошибка', () => {
    expect(inspectionState(undefined, today).tone).toBe('none');
    expect(inspectionState('скоро', today)).toMatchObject({ tone: 'none', text: 'скоро' });
  });
});
