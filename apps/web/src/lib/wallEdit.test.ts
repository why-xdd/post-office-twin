import { describe, expect, it } from 'vitest';
import { outlinePoints, polygonArea } from './planFacts';
import { DEFAULT_ROOM, buildRoom } from './roomBuilder';
import { addOpening, moveWall, openingPlacement, removeWall, replaceOutline, resizeOpening } from './wallEdit';
import { hostWall } from './room';

const plan = () => buildRoom({ ...DEFAULT_ROOM });
const wallAt = (p: ReturnType<typeof plan>, predicate: (w: NonNullable<ReturnType<typeof plan>['walls']>[number]) => boolean) =>
  p.walls!.find(predicate)!;

describe('сдвиг стены', () => {
  it('правая стена уходит на метр: площадь растёт, углы и дверь едут вместе', () => {
    const base = plan();
    const right = wallAt(base, (w) => w.type === 'outer' && w.from[0] === 12 && w.to[0] === 12);
    const moved = moveWall(base, right.id, 1)!;
    expect(polygonArea(outlinePoints(moved)!)).toBeCloseTo(104);
    const service = moved.doors!.find((d) => d.type === 'service')!;
    expect(service.wall[0]).toBeCloseTo(13);
    expect(hostWall(moved, service.wall)?.id).toBe(right.id);
    const serviceZone = moved.zones!.find((z) => z.type === 'back_office')!;
    expect(Math.max(...serviceZone.polygon.map((p) => p[0]))).toBeCloseTo(13);
  });

  it('перегородка тянется вместе с зонами и внутренней дверью', () => {
    const base = plan();
    const partition = wallAt(base, (w) => w.type === 'partition');
    const moved = moveWall(base, partition.id, -1)!;
    const internal = moved.doors!.find((d) => d.type === 'internal')!;
    expect(internal.wall[0]).toBeCloseTo(7.5);
    const areas = moved.zones!.map((z) => polygonArea(z.polygon));
    expect(areas[0] + areas[1]).toBeCloseTo(96);
    expect(areas[1]).toBeCloseTo(4.5 * 8);
  });

  it('не даёт схлопнуть соседнюю стену', () => {
    const base = plan();
    const right = wallAt(base, (w) => w.type === 'outer' && w.from[0] === 12 && w.to[0] === 12);
    expect(moveWall(base, right.id, -12)).toBeNull();
  });
});

describe('проёмы', () => {
  it('окно в наружной стене по щелчку, с центром у точки щелчка', () => {
    const base = plan();
    const top = wallAt(base, (w) => w.type === 'outer' && w.from[1] === 8 && w.to[1] === 8);
    const result = addOpening(base, top.id, [4, 8.05], 'window');
    expect('plan' in result).toBe(true);
    if (!('plan' in result)) return;
    const win = result.plan.windows!.find((w) => w.id === result.id)!;
    const placement = openingPlacement(top, win.wall);
    expect(placement.width).toBeCloseTo(1.2);
    expect(Math.min(win.wall[0], win.wall[2])).toBeCloseTo(3.4);
  });

  it('окно в перегородку и проём поверх проёма запрещены', () => {
    const base = plan();
    const partition = wallAt(base, (w) => w.type === 'partition');
    expect('error' in addOpening(base, partition.id, [8.5, 2], 'window')).toBe(true);
    const bottom = wallAt(base, (w) => w.type === 'outer' && w.from[1] === 0 && w.to[1] === 0);
    expect('error' in addOpening(base, bottom.id, [1.7, 0], 'door')).toBe(true);
  });

  it('ширина проёма меняется вокруг центра и не выходит за стену', () => {
    const base = plan();
    const wide = resizeOpening(base, 'door', 'd_main', { width: 2 });
    expect('plan' in wide && wide.plan.doors!.find((d) => d.id === 'd_main')!.wall[0]).toBeCloseTo(0.75);
    expect('error' in resizeOpening(base, 'door', 'd_main', { offset: 11 })).toBe(true);
  });

  it('удаление стены убирает проёмы на ней', () => {
    const base = plan();
    const partition = wallAt(base, (w) => w.type === 'partition');
    const next = removeWall(base, partition.id);
    expect(next.doors!.some((d) => d.type === 'internal')).toBe(false);
  });
});

describe('замена контура', () => {
  it('новый контур сохраняет перегородку и проёмы, которые остались на стенах', () => {
    const base = plan();
    const { plan: next, droppedOpenings } = replaceOutline(base, [
      [0, 0],
      [12, 0],
      [12, 10],
      [0, 10],
    ]);
    expect(polygonArea(outlinePoints(next)!)).toBeCloseTo(120);
    expect(next.walls!.filter((w) => w.type === 'partition')).toHaveLength(1);
    expect(droppedOpenings).toBe(0);
  });
});

describe('наклонная стена', () => {
  // Трапеция: правая стена наклонная, от (10, 0) к (8, 6).
  const trapezoid = () =>
    ({
      ops: { id: 'ops', name: 'Трапеция', bbox: { width_m: 10, height_m: 6 } },
      walls: [
        { id: 'bottom', from: [0, 0], to: [10, 0], type: 'outer' },
        { id: 'slant', from: [10, 0], to: [8, 6], type: 'outer' },
        { id: 'top', from: [8, 6], to: [0, 6], type: 'outer' },
        { id: 'left', from: [0, 6], to: [0, 0], type: 'outer' },
      ],
      zones: [],
      equipment: [],
    }) as unknown as ReturnType<typeof plan>;

  it('сдвигается поперёк себя, соседние стены сохраняют направление', () => {
    const moved = moveWall(trapezoid(), 'slant', 1)!;
    const slant = moved.walls!.find((w) => w.id === 'slant')!;
    const bottom = moved.walls!.find((w) => w.id === 'bottom')!;
    const top = moved.walls!.find((w) => w.id === 'top')!;
    // Нижняя и верхняя остались горизонтальными, наклон стены тот же.
    expect(bottom.to[1]).toBeCloseTo(0);
    expect(top.from[1]).toBeCloseTo(6);
    const slope = (w: typeof slant) => (w.to[0] - w.from[0]) / (w.to[1] - w.from[1]);
    expect(slope(slant)).toBeCloseTo(-2 / 6);
    // Расстояние между старой и новой линией — ровно метр по нормали.
    const length = Math.hypot(2, 6);
    const distance = ((slant.from[0] - 10) * 6 + (slant.from[1] - 0) * 2) / length;
    expect(Math.abs(distance)).toBeCloseTo(1);
  });
});
