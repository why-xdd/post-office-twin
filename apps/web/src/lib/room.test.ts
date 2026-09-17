import { describe, expect, it } from 'vitest';
import { withTransform } from './equipmentOps';
import type { Equipment, Plan } from './plan';
import { fitsRoom, hostWall, placeInRoom } from './room';

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
