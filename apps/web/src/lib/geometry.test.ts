import { describe, expect, it } from 'vitest';
import { equipmentAt } from './geometry';
import type { Plan } from './plan';

const plan = {
  ops: { id: 'ops', name: 'Тест', bbox: { width_m: 10, height_m: 8 } },
  equipment: [
    { id: 'cabinet', type: 'cabinet', position: [2, 2], size_m: [0.4, 0.4] },
    { id: 'desk', type: 'desk', position: [5, 5], size_m: [1.2, 0.6] },
  ],
} as unknown as Plan;

describe('объект под пальцем', () => {
  it('без допуска промах мимо объекта — пустое место', () => {
    expect(equipmentAt(plan, [2.3, 2])).toBeNull();
  });

  it('с допуском касание рядом ловит ближайший мелкий объект', () => {
    expect(equipmentAt(plan, [2.3, 2], 0.15)?.id).toBe('cabinet');
  });

  it('допуск не тянется к объекту дальше себя', () => {
    expect(equipmentAt(plan, [3, 3], 0.15)).toBeNull();
  });

  it('попадание внутрь объекта не зависит от допуска', () => {
    expect(equipmentAt(plan, [5, 5], 0.15)?.id).toBe('desk');
  });
});
