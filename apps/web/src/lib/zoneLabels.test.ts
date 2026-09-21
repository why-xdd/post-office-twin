import { describe, expect, it } from 'vitest';
import { pointInPolygon } from './geometry';
import type { Plan } from './plan';
import { zoneLabelPoints } from './zoneLabels';

const hall: [number, number][] = [[0, 2.5], [3.5, 2.5], [3.5, 0], [10.5, 0], [10.5, 9], [0, 9]];
const queue: [number, number][] = [[6.2, 1.8], [8.4, 1.8], [8.4, 7.8], [6.2, 7.8]];

function plan(): Plan {
  return {
    ops: { id: 't', name: 'Тест' },
    walls: [],
    zones: [
      { id: 'hall', type: 'client_area', label: 'Операционный зал', polygon: hall },
      { id: 'queue', type: 'queue_accumulator', label: 'Накопитель', polygon: queue },
    ],
    equipment: [{ id: 'eq', type: 'storage_rack', position: [2, 6], size_m: [2, 0.6], rotation_deg: 0 }],
  } as Plan;
}

describe('подписи зон', () => {
  it('ставит подпись Г-образной зоны внутри неё, а не на вложенной зоне', () => {
    const points = zoneLabelPoints(plan());
    const hallPoint = points.get('hall')!;
    expect(pointInPolygon(hallPoint, hall)).toBe(true);
    expect(pointInPolygon(hallPoint, queue)).toBe(false);
  });

  it('разводит подписи вложенной и объемлющей зон', () => {
    const points = zoneLabelPoints(plan());
    const [a, b] = [points.get('hall')!, points.get('queue')!];
    expect(Math.hypot(a[0] - b[0], a[1] - b[1])).toBeGreaterThan(2);
  });

  it('не кладёт подпись на оборудование', () => {
    const point = zoneLabelPoints(plan()).get('hall')!;
    const onRack = point[0] > 0.9 && point[0] < 3.1 && point[1] > 5.6 && point[1] < 6.4;
    expect(onRack).toBe(false);
  });
});
