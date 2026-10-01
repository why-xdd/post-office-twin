/**
 * Самое узкое место между объектами: где на плане рисовать размер «0,74 м».
 *
 * Число берётся из проверки, а здесь ищется только место: кратчайший отрезок между объектами
 * нарушения, а для одного объекта — между ним и ближайшим соседом или стеной.
 */

import { equipmentCorners, type Equipment, type Plan, type Point } from './plan';

export interface Gap {
  a: Point;
  b: Point;
  distance: number;
}

function closestOnSegment([px, py]: Point, [ax, ay]: Point, [bx, by]: Point): Point {
  const dx = bx - ax;
  const dy = by - ay;
  const lengthSq = dx * dx + dy * dy;
  const t = lengthSq === 0 ? 0 : Math.max(0, Math.min(1, ((px - ax) * dx + (py - ay) * dy) / lengthSq));
  return [ax + dx * t, ay + dy * t];
}

function edges(points: Point[]): [Point, Point][] {
  return points.map((p, i) => [p, points[(i + 1) % points.length]]);
}

/** Все концы кратчайших отрезков между двумя гранями: у параллельных граней их несколько. */
function segmentGaps(p1: Point, p2: Point, q1: Point, q2: Point): Gap[] {
  const out: Gap[] = [];
  for (const p of [p1, p2]) {
    const b = closestOnSegment(p, q1, q2);
    out.push({ a: p, b, distance: Math.hypot(p[0] - b[0], p[1] - b[1]) });
  }
  for (const q of [q1, q2]) {
    const a = closestOnSegment(q, p1, p2);
    out.push({ a, b: q, distance: Math.hypot(a[0] - q[0], a[1] - q[1]) });
  }
  return out;
}

/**
 * Кратчайший просвет между фигурами. У параллельных граней кратчайших отрезков много —
 * берётся средний из них: размер встаёт посередине общего участка, а не у самого края.
 */
function shapeGap(first: [Point, Point][], second: [Point, Point][]): Gap | null {
  const all: Gap[] = [];
  for (const [p1, p2] of first) for (const [q1, q2] of second) all.push(...segmentGaps(p1, p2, q1, q2));
  if (all.length === 0) return null;
  const distance = Math.min(...all.map((g) => g.distance));
  const ties = all.filter((g) => g.distance - distance < 1e-3);
  const mean = (pick: (g: Gap) => Point): Point => [
    ties.reduce((sum, g) => sum + pick(g)[0], 0) / ties.length,
    ties.reduce((sum, g) => sum + pick(g)[1], 0) / ties.length,
  ];
  return { a: mean((g) => g.a), b: mean((g) => g.b), distance };
}

/**
 * Место самого узкого просвета для нарушения с участием objects. Касающиеся и
 * перекрывающиеся объекты пропускаются: у них просвета нет, рисовать нечего.
 */
export function narrowestGap(plan: Plan, objects: string[], isFloor: (item: Equipment) => boolean): Gap | null {
  const items = (plan.equipment ?? []).filter((item) => objects.includes(item.id) && isFloor(item));
  if (items.length === 0) return null;
  const shapes = new Map(items.map((item) => [item.id, edges(equipmentCorners(item))]));
  const usable = (gap: Gap | null) => (gap && gap.distance > 0.02 ? gap : null);
  let best: Gap | null = null;
  const keep = (gap: Gap | null) => {
    if (gap && (!best || gap.distance < best.distance)) best = gap;
  };

  if (items.length >= 2) {
    for (let i = 0; i < items.length; i += 1) {
      for (let j = i + 1; j < items.length; j += 1) keep(usable(shapeGap(shapes.get(items[i].id)!, shapes.get(items[j].id)!)));
    }
    if (best) return best;
  }

  // Один объект: просвет до ближайшего соседа или стены.
  const others = (plan.equipment ?? []).filter((item) => !objects.includes(item.id) && isFloor(item));
  const walls: [Point, Point][] = (plan.walls ?? []).map((wall) => [wall.from, wall.to]);
  for (const item of items) {
    const own = shapes.get(item.id)!;
    for (const other of others) keep(usable(shapeGap(own, edges(equipmentCorners(other)))));
    keep(usable(shapeGap(own, walls)));
  }
  return best;
}
