/**
 * Выравнивание и распределение группы объектов. Считается по габаритам на плане с учётом
 * поворота: «по левому краю» ставит на одну линию левые края рамок, а не центры.
 * Ось Y плана смотрит вверх, поэтому «верх» — это большие Y.
 */

import { round3 } from './geometry';
import { equipmentCorners, type Equipment, type Point } from './plan';

export type AlignMode = 'left' | 'centerX' | 'right' | 'top' | 'centerY' | 'bottom' | 'spreadX' | 'spreadY';

interface Box {
  item: Equipment;
  minX: number;
  maxX: number;
  minY: number;
  maxY: number;
}

function box(item: Equipment): Box {
  const corners = equipmentCorners(item);
  const xs = corners.map((c) => c[0]);
  const ys = corners.map((c) => c[1]);
  return { item, minX: Math.min(...xs), maxX: Math.max(...xs), minY: Math.min(...ys), maxY: Math.max(...ys) };
}

/** Новые центры объектов. Объект, которому двигаться некуда, в ответ не попадает. */
export function alignGroup(items: Equipment[], mode: AlignMode): Map<string, Point> {
  const boxes = items.map(box);
  const moves = new Map<string, Point>();
  if (boxes.length < 2) return moves;
  const move = ({ item }: Box, dx: number, dy: number) => {
    if (Math.abs(dx) < 1e-6 && Math.abs(dy) < 1e-6) return;
    moves.set(item.id, [round3(item.position[0] + dx), round3(item.position[1] + dy)]);
  };
  const minX = Math.min(...boxes.map((b) => b.minX));
  const maxX = Math.max(...boxes.map((b) => b.maxX));
  const minY = Math.min(...boxes.map((b) => b.minY));
  const maxY = Math.max(...boxes.map((b) => b.maxY));

  switch (mode) {
    case 'left':
      boxes.forEach((b) => move(b, minX - b.minX, 0));
      break;
    case 'right':
      boxes.forEach((b) => move(b, maxX - b.maxX, 0));
      break;
    case 'centerX':
      boxes.forEach((b) => move(b, (minX + maxX) / 2 - (b.minX + b.maxX) / 2, 0));
      break;
    case 'top':
      boxes.forEach((b) => move(b, 0, maxY - b.maxY));
      break;
    case 'bottom':
      boxes.forEach((b) => move(b, 0, minY - b.minY));
      break;
    case 'centerY':
      boxes.forEach((b) => move(b, 0, (minY + maxY) / 2 - (b.minY + b.maxY) / 2));
      break;
    case 'spreadX':
      spread(boxes, 'x', move);
      break;
    case 'spreadY':
      spread(boxes, 'y', move);
      break;
  }
  return moves;
}

/**
 * Равные промежутки между рамками: крайние объекты стоят, средние встают между ними.
 * Если рамки и так перекрываются по оси, раздвигать некуда — объекты не трогаются.
 */
function spread(boxes: Box[], axis: 'x' | 'y', move: (b: Box, dx: number, dy: number) => void) {
  if (boxes.length < 3) return;
  const lo = (b: Box) => (axis === 'x' ? b.minX : b.minY);
  const hi = (b: Box) => (axis === 'x' ? b.maxX : b.maxY);
  const sorted = [...boxes].sort((a, b) => lo(a) + hi(a) - (lo(b) + hi(b)));
  const span = hi(sorted[sorted.length - 1]) - lo(sorted[0]);
  const occupied = sorted.reduce((sum, b) => sum + hi(b) - lo(b), 0);
  const gap = (span - occupied) / (sorted.length - 1);
  if (gap < 0) return;
  let cursor = lo(sorted[0]);
  for (const b of sorted) {
    const shift = cursor - lo(b);
    if (axis === 'x') move(b, shift, 0);
    else move(b, 0, shift);
    cursor += hi(b) - lo(b) + gap;
  }
}
