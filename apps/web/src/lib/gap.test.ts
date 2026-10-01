import { describe, expect, it } from 'vitest';
import { narrowestGap } from './gap';
import type { Plan } from './plan';

const plan = {
  ops: { id: 'o', name: 'Тест', bbox: { width_m: 10, height_m: 8 } },
  walls: [
    { id: 'w1', from: [0, 0], to: [10, 0], type: 'outer' },
    { id: 'w2', from: [10, 0], to: [10, 8], type: 'outer' },
  ],
  equipment: [
    { id: 'a', type: 'atm', position: [2, 2], size_m: [1, 1] },
    { id: 'b', type: 'waiting_seat', position: [3.75, 2], size_m: [1, 1] },
    { id: 'c', type: 'safe', position: [9.2, 5], size_m: [1, 1] },
  ],
} as unknown as Plan;

describe('место узкого просвета', () => {
  it('между двумя объектами нарушения — кратчайший отрезок между ними', () => {
    const gap = narrowestGap(plan, ['a', 'b'], () => true)!;
    expect(gap.distance).toBeCloseTo(0.75);
    expect(gap.a[0]).toBeCloseTo(2.5);
    expect(gap.b[0]).toBeCloseTo(3.25);
    // Грани параллельны: размер посередине общего участка, на высоте центров.
    expect(gap.a[1]).toBeCloseTo(2);
  });

  it('у одного объекта — до ближайшей стены или соседа', () => {
    const gap = narrowestGap(plan, ['c'], () => true)!;
    expect(gap.distance).toBeCloseTo(0.3);
  });
});
