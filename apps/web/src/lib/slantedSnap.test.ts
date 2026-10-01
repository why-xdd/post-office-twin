import { describe, expect, it } from 'vitest';
import { equipmentCorners, type Plan } from './plan';
import { snapToSlantedWall } from './room';

const plan = {
  ops: { id: 'ops', name: 'Трапеция', bbox: { width_m: 10, height_m: 6 } },
  walls: [
    { id: 'bottom', from: [0, 0], to: [10, 0], type: 'outer' },
    { id: 'slant', from: [10, 0], to: [8, 6], type: 'outer' },
    { id: 'top', from: [8, 6], to: [0, 6], type: 'outer' },
    { id: 'left', from: [0, 6], to: [0, 0], type: 'outer' },
  ],
  zones: [],
  equipment: [],
} as unknown as Plan;

describe('объект у наклонной стены', () => {
  it('разворачивается вдоль стены и встаёт к ней вплотную', () => {
    const rack = { id: 'r', type: 'storage_rack', position: [8.3, 3], size_m: [2, 0.5], rotation_deg: 90 } as never;
    const snapped = snapToSlantedWall(plan, rack)!;
    expect(snapped).not.toBeNull();
    const wallAngle = (Math.atan2(6, -2) * 180) / Math.PI;
    const diff = Math.abs(((snapped.rotation_deg - wallAngle) % 90 + 90) % 90);
    expect(Math.min(diff, 90 - diff)).toBeLessThan(0.2);
    // Ближайший угол объекта лежит на стене: расстояние до линии около нуля, все углы внутри.
    const corners = equipmentCorners({ ...(rack as object), ...snapped } as never);
    // Расстояние до прямой через (10, 0) и (8, 6); со стороны помещения оно отрицательное.
    const distances = corners.map(([x, y]) => ((x - 10) * 6 + y * 2) / Math.hypot(2, 6));
    expect(Math.max(...distances)).toBeCloseTo(0, 2);
  });

  it('далеко от стены ничего не делает', () => {
    const rack = { id: 'r', type: 'storage_rack', position: [3, 3], size_m: [2, 0.5], rotation_deg: 0 } as never;
    expect(snapToSlantedWall(plan, rack)).toBeNull();
  });
});
