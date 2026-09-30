import { describe, expect, it } from 'vitest';
import { alignGroup } from './align';
import type { Equipment } from './plan';

const item = (id: string, position: [number, number], size: [number, number], rotation = 0): Equipment => ({
  id,
  type: 'cabinet',
  position,
  size_m: size,
  rotation_deg: rotation,
});

describe('выравнивание группы', () => {
  const a = item('a', [1, 1], [1, 0.5]);
  const b = item('b', [3, 2], [2, 1]);

  it('по левому краю ставит левые края рамок на одну линию', () => {
    const moves = alignGroup([a, b], 'left');
    expect(moves.has('a')).toBe(false);
    expect(moves.get('b')).toEqual([1.5, 2]);
  });

  it('по верху — по большим Y: ось плана смотрит вверх', () => {
    const moves = alignGroup([a, b], 'top');
    expect(moves.get('a')).toEqual([1, 2.25]);
    expect(moves.has('b')).toBe(false);
  });

  it('учитывает поворот: у повёрнутого на 90° объекта ширина и глубина меняются местами', () => {
    const turned = item('t', [5, 1], [2, 0.5], 90);
    const moves = alignGroup([a, turned], 'right');
    // Правый край повёрнутого — 5 + 0,25; скамья a встаёт правым краем туда же.
    expect(moves.get('a')).toEqual([4.75, 1]);
  });

  it('распределяет средние объекты с равными промежутками', () => {
    const left = item('l', [0.5, 0], [1, 1]);
    const middle = item('m', [2, 0], [1, 1]);
    const right = item('r', [6.5, 0], [1, 1]);
    const moves = alignGroup([right, middle, left], 'spreadX');
    // Промежутки по 2 м: 1 → 3, 4 → 6.
    expect(moves.get('m')).toEqual([3.5, 0]);
    expect(moves.has('l')).toBe(false);
    expect(moves.has('r')).toBe(false);
  });

  it('не раздвигает рамки, которые и так перекрываются', () => {
    const moves = alignGroup([item('x', [0, 0], [3, 1]), item('y', [1, 0], [3, 1]), item('z', [2, 0], [3, 1])], 'spreadX');
    expect(moves.size).toBe(0);
  });
});
