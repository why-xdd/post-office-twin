import { describe, expect, it } from 'vitest';
import { withTransform } from './equipmentOps';
import type { Equipment, Plan } from './plan';
import { alignToNeighbours, fitsRoom, hostWall, placeInRoom } from './room';

function room(): Plan {
  return {
    ops: { id: 't', name: 'Тест' },
    walls: [
      { id: 's', from: [0, 0], to: [10, 0], type: 'outer' },
      { id: 'e', from: [10, 0], to: [10, 6], type: 'outer' },
      { id: 'n', from: [10, 6], to: [0, 6], type: 'outer' },
      { id: 'w', from: [0, 6], to: [0, 0], type: 'outer' },
      { id: 'p', from: [7, 0], to: [7, 6], type: 'partition' },
    ],
    doors: [{ id: 'd', wall: [2, 6, 3.5, 6], type: 'entrance', width_m: 1.5 }],
    equipment: [],
  } as Plan;
}

const rack = (position: [number, number]): Equipment => ({
  id: 'r',
  type: 'storage_rack',
  position,
  size_m: [2, 0.6],
  rotation_deg: 0,
});

describe('привязка к стенам', () => {
  it('прижимает к внутренней грани наружной стены, а не к её оси', () => {
    const [, y] = placeInRoom(room(), rack([3, 0.42]), null);
    expect(y).toBeCloseTo(0.3);
  });

  it('убирает небольшой заезд в стену', () => {
    const [, y] = placeInRoom(room(), rack([3, 0.2]), null);
    expect(y).toBeCloseTo(0.3);
  });

  it('прижимает к грани перегородки с учётом её толщины', () => {
    const [x] = placeInRoom(room(), rack([5.9, 3]), null);
    expect(x).toBeCloseTo(7 - 0.05 - 1);
  });

  it('не пускает объект за контур: остаётся прежнее положение', () => {
    const previous: [number, number] = [3, 1];
    const [x, y] = placeInRoom(room(), rack([3, -1]), previous);
    expect(fitsRoom(room(), rack([x, y]))).toBe(true);
    expect(y).toBeGreaterThanOrEqual(0.3);
  });

  it('скользит вдоль стены, когда упирается одной осью', () => {
    const [x, y] = placeInRoom(room(), rack([4, -0.5]), [3, 0.3]);
    expect(x).toBeCloseTo(4);
    expect(y).toBeCloseTo(0.3);
  });

  it('объект поперёк перегородки не помещается', () => {
    expect(fitsRoom(room(), rack([7, 3]))).toBe(false);
    expect(fitsRoom(room(), rack([3, 3]))).toBe(true);
  });
});

describe('проёмы', () => {
  it('находит стену, на которой лежит дверь', () => {
    expect(hostWall(room(), [2, 6, 3.5, 6])?.id).toBe('n');
  });
});

describe('место оператора', () => {
  const cashier: Equipment = {
    id: 'c',
    type: 'cashier_window',
    position: [2, 2],
    size_m: [1.2, 0.8],
    rotation_deg: 0,
    operator_position: [2, 3],
  };

  it('сдвигается вместе с объектом', () => {
    expect(withTransform(cashier, { position: [4, 2.5] }).operator_position).toEqual([4, 3.5]);
  });

  it('поворачивается вокруг центра объекта по часовой стрелке', () => {
    const [ox, oy] = withTransform(cashier, { rotation_deg: 90 }).operator_position!;
    expect(ox).toBeCloseTo(3);
    expect(oy).toBeCloseTo(2);
  });
});

/**
 * Выравнивание по соседям: объект встаёт с ними на одну линию, а линия это показывает.
 *
 * В отделении вещи стоят рядами, и разница в пять сантиметров видна на распечатке, но не на
 * экране. Проверяется, что выравнивание срабатывает по середине и по краю, не срабатывает
 * издалека и не двигает повёрнутый объект, для которого «край» уже не по осям.
 */
const bare = (items: Equipment[]): Plan =>
  ({
    ops: { id: 'ops', name: 'Тест', bbox: { width_m: 20, height_m: 12 } },
    walls: [],
    zones: [],
    equipment: items,
  }) as unknown as Plan;

const bench = (id: string, position: [number, number], rotation = 0): Equipment =>
  ({ id, type: 'waiting_seat', label: id, position, size_m: [2, 0.5], rotation_deg: rotation }) as Equipment;

describe('выравнивание по соседям', () => {
  it('ставит объект на одну линию по середине', () => {
    const moved = bench('b2', [5, 4.04]);
    const result = alignToNeighbours(bare([bench('b1', [2, 4]), moved]), moved, 0.1);
    expect(result.position[1]).toBe(4);
    expect(result.guides.some((g) => g.axis === 'y' && g.at === 4)).toBe(true);
    expect(result.guides.every((g) => g.axis !== 'x')).toBe(true);
  });

  it('ставит объект на одну линию по краю', () => {
    // Стеллаж 4 м стоит от 1 до 5 по x. Скамья 2 м левым краем на 1,04 — дотягивается до 1.
    const rack = { ...bench('r1', [3, 8]), size_m: [4, 0.5] } as Equipment;
    const moved = bench('b2', [2.04, 4]);
    const result = alignToNeighbours(bare([rack, moved]), moved, 0.1);
    expect(result.position[0]).toBe(2);
    expect(result.guides.some((g) => g.axis === 'x' && g.at === 1)).toBe(true);
  });

  it('не тянет издалека', () => {
    const moved = bench('b2', [5, 5]);
    const result = alignToNeighbours(bare([bench('b1', [2, 4]), moved]), moved, 0.1);
    expect(result.position).toEqual([5, 5]);
    expect(result.guides).toEqual([]);
  });

  it('не трогает объект, повёрнутый не по осям', () => {
    const moved = bench('b2', [5, 4.04], 30);
    const result = alignToNeighbours(bare([bench('b1', [2, 4]), moved]), moved, 0.1);
    expect(result.position).toEqual([5, 4.04]);
    expect(result.guides).toEqual([]);
  });

  it('линия тянется от соседа до объекта', () => {
    const moved = bench('b2', [8, 4.03]);
    const result = alignToNeighbours(bare([bench('b1', [2, 4]), moved]), moved, 0.1);
    const guide = result.guides.find((g) => g.axis === 'y');
    expect(guide).toBeDefined();
    expect(guide!.from).toBeLessThanOrEqual(1);
    expect(guide!.to).toBeGreaterThanOrEqual(9);
  });
});
