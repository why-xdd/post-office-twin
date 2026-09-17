/**
 * Геометрия на клиенте: вид, привязка, попадание.
 *
 * Координаты плана — метры, ось Y вверх. Экран — пиксели, ось Y вниз. Всё, что рисуется
 * и ловит указатель, проходит через один вид, чтобы редактор и сервер считали одинаково.
 */

import type { Equipment, Plan, Point, Zone } from './plan';
import { equipmentCorners } from './plan';

export interface View {
  /** Пикселей на метр. */
  scale: number;
  /** Экранные координаты точки (0, 0) плана. */
  originX: number;
  originY: number;
}

export const MIN_SCALE = 12;
export const MAX_SCALE = 320;

export function toScreen(view: View, [x, y]: Point): [number, number] {
  return [view.originX + x * view.scale, view.originY - y * view.scale];
}

export function toWorld(view: View, sx: number, sy: number): Point {
  return [(sx - view.originX) / view.scale, (view.originY - sy) / view.scale];
}

export function screenPath(view: View, points: Point[], close = true): string {
  if (points.length === 0) return '';
  const [first, ...rest] = points.map((p) => toScreen(view, p));
  return `M${first[0].toFixed(1)} ${first[1].toFixed(1)}${rest
    .map(([x, y]) => `L${x.toFixed(1)} ${y.toFixed(1)}`)
    .join('')}${close ? 'Z' : ''}`;
}

/** Вид, при котором план целиком помещается в рамку с полями. */
export function fitView(
  bounds: { minX: number; minY: number; maxX: number; maxY: number },
  width: number,
  height: number,
  padding = 56,
): View {
  const w = Math.max(bounds.maxX - bounds.minX, 1);
  const h = Math.max(bounds.maxY - bounds.minY, 1);
  const scale = clamp(Math.min((width - padding * 2) / w, (height - padding * 2) / h), MIN_SCALE, MAX_SCALE);
  const originX = (width - w * scale) / 2 - bounds.minX * scale;
  const originY = (height + h * scale) / 2 + bounds.minY * scale;
  return { scale, originX, originY };
}

/** Масштаб вокруг точки экрана: точка под указателем остаётся на месте. */
export function zoomAt(view: View, sx: number, sy: number, factor: number): View {
  const scale = clamp(view.scale * factor, MIN_SCALE, MAX_SCALE);
  const [wx, wy] = toWorld(view, sx, sy);
  return { scale, originX: sx - wx * scale, originY: sy + wy * scale };
}

export function clamp(value: number, min: number, max: number): number {
  return Math.min(max, Math.max(min, value));
}

export function snap(value: number, step: number): number {
  return Math.round(value / step) * step;
}

export function round3(value: number): number {
  return Math.round(value * 1000) / 1000;
}

export function pointInPolygon([px, py]: Point, polygon: Point[]): boolean {
  let inside = false;
  for (let i = 0, j = polygon.length - 1; i < polygon.length; j = i, i += 1) {
    const [xi, yi] = polygon[i];
    const [xj, yj] = polygon[j];
    if (yi > py !== yj > py && px < ((xj - xi) * (py - yi)) / (yj - yi) + xi) inside = !inside;
  }
  return inside;
}

export function polygonCentroid(points: Point[]): Point {
  let area = 0;
  let cx = 0;
  let cy = 0;
  for (let i = 0; i < points.length; i += 1) {
    const [x1, y1] = points[i];
    const [x2, y2] = points[(i + 1) % points.length];
    const cross = x1 * y2 - x2 * y1;
    area += cross;
    cx += (x1 + x2) * cross;
    cy += (y1 + y2) * cross;
  }
  if (Math.abs(area) < 1e-9) {
    const n = points.length || 1;
    return [points.reduce((s, p) => s + p[0], 0) / n, points.reduce((s, p) => s + p[1], 0) / n];
  }
  return [cx / (3 * area), cy / (3 * area)];
}

/** Наименьшая по площади зона, содержащая точку: зона ожидания лежит внутри клиентской. */
export function zoneAt(plan: Plan, point: Point): Zone | null {
  const containing = (plan.zones ?? []).filter((z) => pointInPolygon(point, z.polygon));
  if (containing.length === 0) return null;
  const area = (z: Zone) => Math.abs(signedArea(z.polygon));
  return containing.reduce((best, z) => (area(z) < area(best) ? z : best));
}

function signedArea(points: Point[]): number {
  let sum = 0;
  for (let i = 0; i < points.length; i += 1) {
    const [x1, y1] = points[i];
    const [x2, y2] = points[(i + 1) % points.length];
    sum += x1 * y2 - x2 * y1;
  }
  return sum / 2;
}

export function equipmentAt(plan: Plan, point: Point): Equipment | null {
  const items = [...(plan.equipment ?? [])].reverse();
  return items.find((item) => pointInPolygon(point, equipmentCorners(item))) ?? null;
}

export function distanceToSegment([px, py]: Point, [ax, ay]: Point, [bx, by]: Point): number {
  const dx = bx - ax;
  const dy = by - ay;
  const lengthSq = dx * dx + dy * dy;
  const t = lengthSq === 0 ? 0 : clamp(((px - ax) * dx + (py - ay) * dy) / lengthSq, 0, 1);
  return Math.hypot(px - (ax + t * dx), py - (ay + t * dy));
}

/**
 * Привязка объекта к стенам: если край габарита ближе порога к стене, объект придвигается
 * вплотную. Работает для осевых стен и объектов без наклона — так ставят оборудование в ОПС.
 */
export function magnetToWalls(plan: Plan, item: Equipment, threshold = 0.15): Point {
  const [cx, cy] = item.position;
  const rotation = ((item.rotation_deg ?? 0) % 180 + 180) % 180;
  const [sx, sy] = rotation === 90 ? [item.size_m[1], item.size_m[0]] : item.size_m;
  if (rotation !== 0 && rotation !== 90) return item.position;
  let x = cx;
  let y = cy;
  for (const wall of plan.walls ?? []) {
    const [ax, ay] = wall.from;
    const [bx, by] = wall.to;
    if (Math.abs(ax - bx) < 1e-6) {
      const within = cy >= Math.min(ay, by) && cy <= Math.max(ay, by);
      if (!within) continue;
      if (Math.abs(cx - sx / 2 - ax) < threshold) x = ax + sx / 2;
      else if (Math.abs(cx + sx / 2 - ax) < threshold) x = ax - sx / 2;
    } else if (Math.abs(ay - by) < 1e-6) {
      const within = cx >= Math.min(ax, bx) && cx <= Math.max(ax, bx);
      if (!within) continue;
      if (Math.abs(cy - sy / 2 - ay) < threshold) y = ay + sy / 2;
      else if (Math.abs(cy + sy / 2 - ay) < threshold) y = ay - sy / 2;
    }
  }
  return [round3(x), round3(y)];
}

export function uniqueId(existing: Iterable<string>, base: string): string {
  const taken = new Set(existing);
  let index = 1;
  while (taken.has(`${base}_${index}`)) index += 1;
  return `${base}_${index}`;
}
