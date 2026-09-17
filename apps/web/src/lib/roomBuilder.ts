/**
 * Построение плана помещения по размерам: контур, наружные стены, вход, служебная зона
 * с перегородкой и дверями.
 *
 * Координаты — метры, начало в левом нижнем углу, ось Y вверх. Линии наружных стен —
 * внутренние грани: площадь помещения равна площади контура.
 */

import type { Door, Plan, Point, Wall, Zone } from './plan';
import { formatNumber } from './planFacts';

export type Side = 'bottom' | 'top' | 'left' | 'right';
export type Corner = 'top-right' | 'top-left' | 'bottom-right' | 'bottom-left';

export interface RoomSpec {
  name: string;
  shape: 'rect' | 'l';
  width: number;
  depth: number;
  cutCorner: Corner;
  cutWidth: number;
  cutDepth: number;
  entranceSide: Side;
  /** От левого конца стены для верхней и нижней, от нижнего — для боковых. */
  entranceOffset: number;
  entranceWidth: number;
  service: boolean;
  serviceSide: Side;
  serviceDepth: number;
  serviceDoor: boolean;
  population: number | null;
}

export const DEFAULT_ROOM: RoomSpec = {
  name: 'Новое отделение',
  shape: 'rect',
  width: 12,
  depth: 8,
  cutCorner: 'top-right',
  cutWidth: 4,
  cutDepth: 3,
  entranceSide: 'bottom',
  entranceOffset: 1,
  entranceWidth: 1.5,
  service: true,
  serviceSide: 'right',
  serviceDepth: 3.5,
  serviceDoor: true,
  population: null,
};

export const SIDE_LABEL: Record<Side, string> = {
  bottom: 'Нижняя',
  top: 'Верхняя',
  left: 'Левая',
  right: 'Правая',
};

export const CORNER_LABEL: Record<Corner, string> = {
  'top-right': 'Правый верхний',
  'top-left': 'Левый верхний',
  'bottom-right': 'Правый нижний',
  'bottom-left': 'Левый нижний',
};

const MIN_SIDE = 3;
const MAX_SIDE = 80;
const MIN_ZONE = 1.5;
const INTERNAL_DOOR_M = 0.9;
const SERVICE_DOOR_M = 1.0;
const EPS = 1e-6;

export type RoomErrors = Partial<Record<keyof RoomSpec, string>>;

function outlineOf(spec: RoomSpec): Point[] {
  const { width: w, depth: d } = spec;
  if (spec.shape === 'rect') {
    return [
      [0, 0],
      [w, 0],
      [w, d],
      [0, d],
    ];
  }
  const cw = spec.cutWidth;
  const cd = spec.cutDepth;
  switch (spec.cutCorner) {
    case 'top-right':
      return [[0, 0], [w, 0], [w, d - cd], [w - cw, d - cd], [w - cw, d], [0, d]];
    case 'top-left':
      return [[0, 0], [w, 0], [w, d], [cw, d], [cw, d - cd], [0, d - cd]];
    case 'bottom-right':
      return [[0, 0], [w - cw, 0], [w - cw, cd], [w, cd], [w, d], [0, d]];
    case 'bottom-left':
      return [[cw, 0], [w, 0], [w, d], [0, d], [0, cd], [cw, cd]];
  }
}

interface Edge {
  from: Point;
  to: Point;
}

function edges(points: Point[]): Edge[] {
  return points.map((from, i) => ({ from, to: points[(i + 1) % points.length] }));
}

function onSide(edge: Edge, side: Side, spec: RoomSpec): boolean {
  const { from, to } = edge;
  switch (side) {
    case 'bottom':
      return Math.abs(from[1]) < EPS && Math.abs(to[1]) < EPS;
    case 'top':
      return Math.abs(from[1] - spec.depth) < EPS && Math.abs(to[1] - spec.depth) < EPS;
    case 'left':
      return Math.abs(from[0]) < EPS && Math.abs(to[0]) < EPS;
    case 'right':
      return Math.abs(from[0] - spec.width) < EPS && Math.abs(to[0] - spec.width) < EPS;
  }
}

/** Самый длинный участок стены на стороне: у Г-образного помещения их может быть два. */
function sideEdge(points: Point[], side: Side, spec: RoomSpec): { start: Point; end: Point; length: number } | null {
  const candidates = edges(points).filter((edge) => onSide(edge, side, spec));
  if (candidates.length === 0) return null;
  const longest = candidates.reduce((a, b) => (length(b) > length(a) ? b : a));
  // Начало участка — левый конец для горизонтальной стены, нижний — для вертикальной.
  const horizontal = side === 'bottom' || side === 'top';
  const [start, end] =
    (horizontal ? longest.from[0] <= longest.to[0] : longest.from[1] <= longest.to[1])
      ? [longest.from, longest.to]
      : [longest.to, longest.from];
  return { start, end, length: length(longest) };
}

function length(edge: Edge): number {
  return Math.hypot(edge.to[0] - edge.from[0], edge.to[1] - edge.from[1]);
}

function pointAlong(start: Point, end: Point, distance: number): Point {
  const total = Math.hypot(end[0] - start[0], end[1] - start[1]) || 1;
  return [round(start[0] + ((end[0] - start[0]) * distance) / total), round(start[1] + ((end[1] - start[1]) * distance) / total)];
}

function round(value: number): number {
  return Math.round(value * 1000) / 1000;
}

/** Граница служебной зоны: полуплоскость у выбранной стороны. */
function serviceCut(spec: RoomSpec): { axis: 0 | 1; at: number; keepAbove: boolean } {
  switch (spec.serviceSide) {
    case 'right':
      return { axis: 0, at: spec.width - spec.serviceDepth, keepAbove: true };
    case 'left':
      return { axis: 0, at: spec.serviceDepth, keepAbove: false };
    case 'top':
      return { axis: 1, at: spec.depth - spec.serviceDepth, keepAbove: true };
    case 'bottom':
      return { axis: 1, at: spec.serviceDepth, keepAbove: false };
  }
}

/** Отсечение многоугольника полуплоскостью (алгоритм Сазерленда — Ходжмена). */
export function clipPolygon(points: Point[], axis: 0 | 1, at: number, keepAbove: boolean): Point[] {
  const inside = (p: Point) => (keepAbove ? p[axis] >= at - EPS : p[axis] <= at + EPS);
  const result: Point[] = [];
  for (let i = 0; i < points.length; i += 1) {
    const current = points[i];
    const previous = points[(i + points.length - 1) % points.length];
    const crossing = (): Point => {
      const t = (at - previous[axis]) / (current[axis] - previous[axis]);
      const p: Point = [previous[0] + (current[0] - previous[0]) * t, previous[1] + (current[1] - previous[1]) * t];
      p[axis] = at;
      return [round(p[0]), round(p[1])];
    };
    if (inside(current)) {
      if (!inside(previous)) result.push(crossing());
      result.push(current);
    } else if (inside(previous)) {
      result.push(crossing());
    }
  }
  // Повторяющиеся соседние вершины появляются, когда вершина лежит на линии отсечения.
  return result.filter((p, i) => {
    const next = result[(i + 1) % result.length];
    return Math.abs(p[0] - next[0]) > EPS || Math.abs(p[1] - next[1]) > EPS;
  });
}

function polygonArea(points: Point[]): number {
  let sum = 0;
  points.forEach(([x1, y1], i) => {
    const [x2, y2] = points[(i + 1) % points.length];
    sum += x1 * y2 - x2 * y1;
  });
  return Math.abs(sum) / 2;
}

export function validateRoom(spec: RoomSpec): RoomErrors {
  const errors: RoomErrors = {};
  const range = (field: keyof RoomSpec, value: number, min: number, max: number, what: string, name: string) => {
    if (!Number.isFinite(value)) errors[field] = `Укажите ${what}.`;
    else if (value < min || value > max) errors[field] = `${name} — от ${formatNumber(min)} до ${formatNumber(max)} м.`;
  };
  if (!spec.name.trim()) errors.name = 'Укажите название отделения.';
  range('width', spec.width, MIN_SIDE, MAX_SIDE, 'ширину', 'Ширина');
  range('depth', spec.depth, MIN_SIDE, MAX_SIDE, 'глубину', 'Глубина');
  if (spec.shape === 'l') {
    range('cutWidth', spec.cutWidth, 1, Math.max(1, spec.width - MIN_SIDE), 'ширину выреза', 'Ширина выреза');
    range('cutDepth', spec.cutDepth, 1, Math.max(1, spec.depth - MIN_SIDE), 'глубину выреза', 'Глубина выреза');
  }
  if (Object.keys(errors).length > 0) return errors;

  const outline = outlineOf(spec);
  range('entranceWidth', spec.entranceWidth, 0.9, 4, 'ширину входа', 'Ширина входа');
  const edge = sideEdge(outline, spec.entranceSide, spec);
  if (!edge) errors.entranceSide = 'На этой стороне нет стены.';
  else if (spec.entranceOffset < 0 || spec.entranceOffset + spec.entranceWidth > edge.length + EPS) {
    errors.entranceOffset = `Вход должен уместиться на стене длиной ${formatNumber(edge.length)} м: сдвиг от 0 до ${formatNumber(Math.max(0, edge.length - spec.entranceWidth))} м.`;
  }

  if (spec.service) {
    const across = spec.serviceSide === 'left' || spec.serviceSide === 'right' ? spec.width : spec.depth;
    range('serviceDepth', spec.serviceDepth, MIN_ZONE, Math.max(MIN_ZONE, across - MIN_ZONE * 2), 'глубину служебной зоны', 'Глубина служебной зоны');
    if (!errors.serviceDepth && edge && !errors.entranceOffset) {
      const cut = serviceCut(spec);
      const middle = pointAlong(edge.start, edge.end, spec.entranceOffset + spec.entranceWidth / 2);
      const inService = cut.keepAbove ? middle[cut.axis] > cut.at - EPS : middle[cut.axis] < cut.at + EPS;
      if (inService) errors.serviceSide = 'Вход для посетителей попадает в служебную зону — выберите другую сторону.';
      const client = clipPolygon(outline, cut.axis, cut.at, !cut.keepAbove);
      if (polygonArea(client) < 6) errors.serviceDepth = 'Для посетителей почти не остаётся места.';
    }
  }
  return errors;
}

export function buildRoom(spec: RoomSpec): Plan {
  const outline = outlineOf(spec);
  const walls: Wall[] = edges(outline).map((edge, index) => ({
    id: `w_${index + 1}`,
    from: edge.from,
    to: edge.to,
    type: 'outer',
  }));

  const doors: Door[] = [];
  const entrance = sideEdge(outline, spec.entranceSide, spec);
  if (entrance) {
    const a = pointAlong(entrance.start, entrance.end, spec.entranceOffset);
    const b = pointAlong(entrance.start, entrance.end, spec.entranceOffset + spec.entranceWidth);
    doors.push({ id: 'd_main', wall: [a[0], a[1], b[0], b[1]], type: 'entrance', width_m: spec.entranceWidth, label: 'Главный вход' });
  }

  const zones: Zone[] = [];
  if (spec.service) {
    const cut = serviceCut(spec);
    const service = clipPolygon(outline, cut.axis, cut.at, cut.keepAbove);
    const client = clipPolygon(outline, cut.axis, cut.at, !cut.keepAbove);
    zones.push({ id: 'z_client', type: 'client_area', label: 'Клиентская зона', polygon: client });
    zones.push({ id: 'z_service', type: 'back_office', label: 'Служебная зона', polygon: service });

    // Перегородка — рёбра служебной зоны, лежащие на линии отсечения.
    const partitions = edges(service).filter(
      (edge) => Math.abs(edge.from[cut.axis] - cut.at) < EPS && Math.abs(edge.to[cut.axis] - cut.at) < EPS,
    );
    partitions.forEach((edge, index) => {
      walls.push({ id: `w_partition_${index + 1}`, from: edge.from, to: edge.to, type: 'partition', label: 'Перегородка' });
    });
    const longest = partitions.reduce<Edge | null>((best, edge) => (!best || length(edge) > length(best) ? edge : best), null);
    if (longest && length(longest) >= INTERNAL_DOOR_M + 0.4) {
      const middle = length(longest) / 2;
      const a = pointAlong(longest.from, longest.to, middle - INTERNAL_DOOR_M / 2);
      const b = pointAlong(longest.from, longest.to, middle + INTERNAL_DOOR_M / 2);
      doors.push({ id: 'd_internal', wall: [a[0], a[1], b[0], b[1]], type: 'internal', width_m: INTERNAL_DOOR_M, label: 'Дверь в служебную зону' });
    }

    if (spec.serviceDoor) {
      const outer = edges(service).filter((edge) => onSide(edge, spec.serviceSide, spec));
      const target = outer.reduce<Edge | null>((best, edge) => (!best || length(edge) > length(best) ? edge : best), null);
      if (target && length(target) >= SERVICE_DOOR_M + 0.4) {
        const middle = length(target) / 2;
        const a = pointAlong(target.from, target.to, middle - SERVICE_DOOR_M / 2);
        const b = pointAlong(target.from, target.to, middle + SERVICE_DOOR_M / 2);
        doors.push({ id: 'd_service', wall: [a[0], a[1], b[0], b[1]], type: 'service', width_m: SERVICE_DOOR_M, label: 'Служебный вход' });
      }
    }
  } else {
    zones.push({ id: 'z_client', type: 'client_area', label: 'Клиентская зона', polygon: outline });
  }

  return {
    ops: {
      id: `ops_${Date.now().toString(36)}`,
      name: spec.name.trim(),
      scale: '1 unit = 1 meter',
      bbox: { width_m: spec.width, height_m: spec.depth },
      ceiling_height_m: 3,
      ...(spec.population !== null ? { settlement_population: spec.population } : {}),
    },
    walls,
    doors,
    windows: [],
    zones,
    equipment: [],
    flow_hints: { main_entrance: 'd_main', typical_paths: [] },
  };
}

export function roomArea(spec: RoomSpec): number {
  return polygonArea(outlineOf(spec));
}
