import { describe, expect, it } from 'vitest';
import { outlinePoints, polygonArea } from './planFacts';
import { DEFAULT_ROOM, buildRoom, clipPolygon, roomArea, validateRoom, type RoomSpec } from './roomBuilder';
import { hostWall } from './room';

const spec = (patch: Partial<RoomSpec> = {}): RoomSpec => ({ ...DEFAULT_ROOM, ...patch });

describe('мастер помещения', () => {
  it('прямоугольник: замкнутый контур нужной площади и вход на стене', () => {
    const plan = buildRoom(spec({ service: false }));
    const outline = outlinePoints(plan);
    expect(outline).not.toBeNull();
    expect(polygonArea(outline!)).toBeCloseTo(96);
    const entrance = plan.doors!.find((d) => d.type === 'entrance')!;
    expect(hostWall(plan, entrance.wall)).not.toBeNull();
    expect(entrance.wall).toEqual([1, 0, 2.5, 0]);
  });

  it('Г-образное помещение теряет площадь выреза', () => {
    const plan = buildRoom(spec({ shape: 'l', cutWidth: 4, cutDepth: 3, service: false }));
    expect(polygonArea(outlinePoints(plan)!)).toBeCloseTo(96 - 12);
    expect(roomArea(spec({ shape: 'l' }))).toBeCloseTo(84);
  });

  it('служебная зона: перегородка, внутренняя и служебная двери, зоны делят площадь', () => {
    const plan = buildRoom(spec());
    const partitions = plan.walls!.filter((w) => w.type === 'partition');
    expect(partitions).toHaveLength(1);
    expect(partitions[0].from[0]).toBeCloseTo(12 - 3.5);
    const zonesArea = plan.zones!.reduce((sum, z) => sum + polygonArea(z.polygon), 0);
    expect(zonesArea).toBeCloseTo(96);
    const internal = plan.doors!.find((d) => d.type === 'internal')!;
    expect(hostWall(plan, internal.wall)?.type).toBe('partition');
    const service = plan.doors!.find((d) => d.type === 'service')!;
    expect(hostWall(plan, service.wall)?.type).toBe('outer');
  });

  it('Г-образное помещение со служебной зоной у выреза — перегородка до угла выреза', () => {
    const plan = buildRoom(spec({ shape: 'l', cutCorner: 'top-right', serviceSide: 'right', serviceDepth: 3 }));
    const partitions = plan.walls!.filter((w) => w.type === 'partition');
    const total = partitions.reduce((sum, w) => sum + Math.hypot(w.to[0] - w.from[0], w.to[1] - w.from[1]), 0);
    expect(total).toBeCloseTo(8 - 3);
    const zonesArea = plan.zones!.reduce((sum, z) => sum + polygonArea(z.polygon), 0);
    expect(zonesArea).toBeCloseTo(84);
  });

  it('проверка размеров: вход не помещается на стене', () => {
    expect(validateRoom(spec({ entranceOffset: 11 })).entranceOffset).toBeTruthy();
    expect(validateRoom(spec())).toEqual({});
  });

  it('проверка размеров: вход в служебной зоне запрещён', () => {
    expect(validateRoom(spec({ entranceSide: 'right', entranceOffset: 2 })).serviceSide).toBeTruthy();
  });

  it('отсечение полуплоскостью', () => {
    const square: [number, number][] = [[0, 0], [4, 0], [4, 4], [0, 4]];
    expect(polygonArea(clipPolygon(square, 0, 1, true))).toBeCloseTo(12);
  });
});
