/**
 * Правка помещения на плане: сдвиг стены, проёмы на стенах, замена контура.
 *
 * Все функции чистые: получают план и возвращают новый или описание ошибки. Холст и панель
 * свойств только вызывают их внутри commit, поэтому любая правка отменяется одним Ctrl+Z.
 */

import type { Door, Plan, Point, Wall, WindowOpening } from './plan';
import { round3 } from './geometry';
import { hostWall, isOuter } from './room';

const EPS = 1e-3;
/** Стена короче этого после сдвига соседней считается схлопнувшейся. */
export const MIN_WALL_M = 0.3;
const OPENING_MARGIN_M = 0.1;

type Segment = [number, number, number, number];

export function wallAxis(wall: Wall): 'x' | 'y' | null {
  if (Math.abs(wall.from[0] - wall.to[0]) < EPS) return 'x';
  if (Math.abs(wall.from[1] - wall.to[1]) < EPS) return 'y';
  return null;
}

export function wallLength(wall: Wall): number {
  return Math.hypot(wall.to[0] - wall.from[0], wall.to[1] - wall.from[1]);
}

function onSegment([px, py]: Point, [ax, ay]: Point, [bx, by]: Point): boolean {
  const length = Math.hypot(bx - ax, by - ay);
  if (length < EPS) return Math.hypot(px - ax, py - ay) < EPS;
  const cross = Math.abs((bx - ax) * (py - ay) - (by - ay) * (px - ax)) / length;
  const t = ((px - ax) * (bx - ax) + (py - ay) * (by - ay)) / (length * length);
  return cross < EPS && t > -EPS / length && t < 1 + EPS / length;
}

/**
 * Сдвигает осевую стену перпендикулярно себе. Вместе с ней двигаются концы стен, которые
 * на ней лежат (углы и примыкающие перегородки), проёмы на ней и вершины зон на её линии.
 * Возвращает null, если какая-то стена схлопнулась бы.
 */
/**
 * Единичная нормаль стены, по которой её сдвигают. У вертикальной — вдоль X, у горизонтальной —
 * вдоль Y; у наклонной направление выбрано так, чтобы X нормали был положительным.
 */
export function wallNormal(wall: Wall): Point {
  const axis = wallAxis(wall);
  if (axis === 'x') return [1, 0];
  if (axis === 'y') return [0, 1];
  const length = wallLength(wall) || 1;
  let nx = -(wall.to[1] - wall.from[1]) / length;
  let ny = (wall.to[0] - wall.from[0]) / length;
  if (nx < 0) {
    nx = -nx;
    ny = -ny;
  }
  return [nx, ny];
}

/** Пересечение прямой через p1–p2 с прямой через q1–q2; null — прямые параллельны. */
function intersect(p1: Point, p2: Point, q1: Point, q2: Point): Point | null {
  const d1x = p2[0] - p1[0];
  const d1y = p2[1] - p1[1];
  const d2x = q2[0] - q1[0];
  const d2y = q2[1] - q1[1];
  const denominator = d1x * d2y - d1y * d2x;
  if (Math.abs(denominator) < 1e-9) return null;
  const t = ((q1[0] - p1[0]) * d2y - (q1[1] - p1[1]) * d2x) / denominator;
  return [p1[0] + d1x * t, p1[1] + d1y * t];
}

/**
 * Сдвиг стены по её нормали на delta метров. Концы соседних стен едут вдоль самих соседей:
 * примыкающая стена удлиняется или укорачивается, но не меняет наклон — так ведёт себя
 * перепланировка, а не растягивание резинки.
 */
export function moveWall(plan: Plan, wallId: string, delta: number): Plan | null {
  const wall = plan.walls?.find((w) => w.id === wallId);
  if (!wall) return null;
  const [nx, ny] = wallNormal(wall);
  const a = wall.from;
  const b = wall.to;
  const offset = (p: Point): Point => [p[0] + nx * delta, p[1] + ny * delta];
  const movedA = offset(a);
  const movedB = offset(b);
  const same = (p: Point, q: Point) => Math.hypot(p[0] - q[0], p[1] - q[1]) < EPS;

  // Куда уезжает конец: по соседней стене до пересечения со сдвинутой линией.
  const endpoint = (end: Point, moved: Point): Point => {
    const neighbour = (plan.walls ?? []).find((w) => w.id !== wallId && (same(w.from, end) || same(w.to, end)));
    if (!neighbour) return moved;
    const far = same(neighbour.from, end) ? neighbour.to : neighbour.from;
    return intersect(movedA, movedB, far, end) ?? moved;
  };
  const newA = endpoint(a, movedA);
  const newB = endpoint(b, movedB);
  const round = (p: Point): Point => [round3(p[0]), round3(p[1])];
  const shift = (p: Point): Point => {
    if (same(p, a)) return round(newA);
    if (same(p, b)) return round(newB);
    return round(offset(p));
  };
  const touches = (p: Point) => onSegment(p, a, b);

  const walls = (plan.walls ?? []).map((w) =>
    w.id === wallId
      ? { ...w, from: shift(w.from), to: shift(w.to) }
      : { ...w, from: touches(w.from) ? shift(w.from) : w.from, to: touches(w.to) ? shift(w.to) : w.to },
  );
  if (walls.some((w) => wallLength(w) < MIN_WALL_M)) return null;

  const shiftSegment = (segment: Segment): Segment => {
    const [p, q] = [shift([segment[0], segment[1]]), shift([segment[2], segment[3]])];
    return [p[0], p[1], q[0], q[1]];
  };
  const onThisWall = (segment: Segment) => hostWall(plan, segment)?.id === wallId;

  return {
    ...plan,
    walls,
    doors: (plan.doors ?? []).map((d) => (onThisWall(d.wall) ? { ...d, wall: shiftSegment(d.wall) } : d)),
    windows: (plan.windows ?? []).map((w) => (onThisWall(w.wall) ? { ...w, wall: shiftSegment(w.wall) } : w)),
    zones: (plan.zones ?? []).map((z) => ({ ...z, polygon: z.polygon.map((p) => (touches(p) ? shift(p) : p)) })),
  };
}

/** Отступ начала проёма от начала стены и ширина проёма. */
export function openingPlacement(wall: Wall, segment: Segment): { offset: number; width: number } {
  const [ax, ay] = wall.from;
  const length = wallLength(wall) || 1;
  const ux = (wall.to[0] - ax) / length;
  const uy = (wall.to[1] - ay) / length;
  const t1 = (segment[0] - ax) * ux + (segment[1] - ay) * uy;
  const t2 = (segment[2] - ax) * ux + (segment[3] - ay) * uy;
  return { offset: round3(Math.min(t1, t2)), width: round3(Math.abs(t2 - t1)) };
}

function segmentAt(wall: Wall, offset: number, width: number): Segment {
  const length = wallLength(wall) || 1;
  const ux = (wall.to[0] - wall.from[0]) / length;
  const uy = (wall.to[1] - wall.from[1]) / length;
  return [
    round3(wall.from[0] + ux * offset),
    round3(wall.from[1] + uy * offset),
    round3(wall.from[0] + ux * (offset + width)),
    round3(wall.from[1] + uy * (offset + width)),
  ];
}

function openingsOn(plan: Plan, wallId: string, skip?: string): { offset: number; width: number }[] {
  const wall = plan.walls?.find((w) => w.id === wallId);
  if (!wall) return [];
  return [...(plan.doors ?? []), ...(plan.windows ?? [])]
    .filter((o) => o.id !== skip && hostWall(plan, o.wall)?.id === wallId)
    .map((o) => openingPlacement(wall, o.wall));
}

export type OpeningResult = { plan: Plan; id: string } | { error: string };

/**
 * Проём шириной width с центром в ближайшей к point точке стены. Проём сдвигается внутрь
 * стены, если не помещается у её конца, и не может наложиться на другой проём.
 */
export function addOpening(plan: Plan, wallId: string, point: Point, kind: 'door' | 'window'): OpeningResult {
  const wall = plan.walls?.find((w) => w.id === wallId);
  if (!wall) return { error: 'Стена не найдена.' };
  const outer = isOuter(wall);
  if (kind === 'window' && !outer) return { error: 'Окно ставится только в наружную стену.' };
  const hasEntrance = (plan.doors ?? []).some((d) => d.type === 'entrance');
  const type: Door['type'] = !outer ? 'internal' : hasEntrance ? 'service' : 'entrance';
  const width = kind === 'window' ? 1.2 : type === 'entrance' ? 1.2 : type === 'service' ? 1.0 : 0.9;
  const length = wallLength(wall);
  if (length < width + OPENING_MARGIN_M * 2) {
    return { error: `Стена короче ${String(round3(width + OPENING_MARGIN_M * 2)).replace('.', ',')} м — проём не поместится.` };
  }
  const { offset: along } = openingPlacement(wall, [point[0], point[1], point[0], point[1]]);
  const offset = Math.min(Math.max(along - width / 2, OPENING_MARGIN_M), length - width - OPENING_MARGIN_M);
  const clash = openingsOn(plan, wallId).some((o) => offset < o.offset + o.width && o.offset < offset + width);
  if (clash) return { error: 'Здесь уже есть проём. Выберите другое место на стене.' };

  if (kind === 'door') {
    const id = uniqueOpeningId(plan, 'd');
    const labels: Record<string, string> = { entrance: 'Главный вход', service: 'Служебный вход', internal: 'Внутренняя дверь' };
    const door: Door = { id, wall: segmentAt(wall, round3(offset), width), type, width_m: width, label: labels[type] };
    const flow = type === 'entrance' ? { ...(plan.flow_hints ?? {}), main_entrance: id } : plan.flow_hints;
    return { plan: { ...plan, doors: [...(plan.doors ?? []), door], flow_hints: flow }, id };
  }
  const id = uniqueOpeningId(plan, 'win');
  const win: WindowOpening = { id, wall: segmentAt(wall, round3(offset), width), height_m: 1.2 };
  return { plan: { ...plan, windows: [...(plan.windows ?? []), win] }, id };
}

function uniqueOpeningId(plan: Plan, base: string): string {
  const taken = new Set([...(plan.doors ?? []), ...(plan.windows ?? [])].map((o) => o.id));
  let index = 1;
  while (taken.has(`${base}_${index}`)) index += 1;
  return `${base}_${index}`;
}

/** Новая ширина или отступ проёма с проверкой, что он остаётся на стене и не налезает на соседей. */
export function resizeOpening(
  plan: Plan,
  kind: 'door' | 'window',
  id: string,
  patch: { offset?: number; width?: number },
): OpeningResult {
  const list = kind === 'door' ? plan.doors ?? [] : plan.windows ?? [];
  const opening = list.find((o) => o.id === id);
  if (!opening) return { error: 'Проём не найден.' };
  const wall = hostWall(plan, opening.wall);
  if (!wall) return { error: 'Проём не лежит на стене — удалите его и поставьте заново.' };
  const current = openingPlacement(wall, opening.wall);
  const width = patch.width ?? current.width;
  // При смене ширины центр проёма остаётся на месте.
  const offset = patch.offset ?? current.offset + (current.width - width) / 2;
  const length = wallLength(wall);
  if (width < 0.5) return { error: 'Проём не уже 0,5 м.' };
  if (offset < 0 || offset + width > length + EPS) {
    return { error: `На стене длиной ${String(round3(length)).replace('.', ',')} м проём так не поместится.` };
  }
  const clash = openingsOn(plan, wall.id, id).some((o) => offset < o.offset + o.width - EPS && o.offset < offset + width - EPS);
  if (clash) return { error: 'Проём налезает на соседний проём.' };
  const segment = segmentAt(wall, round3(offset), round3(width));
  if (kind === 'door') {
    return { plan: { ...plan, doors: (plan.doors ?? []).map((d) => (d.id === id ? { ...d, wall: segment, width_m: round3(width) } : d)) }, id };
  }
  return { plan: { ...plan, windows: (plan.windows ?? []).map((w) => (w.id === id ? { ...w, wall: segment } : w)) }, id };
}

/**
 * Замена наружного контура. Перегородки остаются; проёмы, которые больше не лежат ни на
 * одной стене, удаляются — их число возвращается, чтобы сообщить об этом.
 */
export function replaceOutline(plan: Plan, points: Point[]): { plan: Plan; droppedOpenings: number } {
  const outer: Wall[] = points.map((from, i) => ({
    id: `w_${i + 1}`,
    from: [round3(from[0]), round3(from[1])],
    to: [round3(points[(i + 1) % points.length][0]), round3(points[(i + 1) % points.length][1])],
    type: 'outer',
  }));
  const partitions = (plan.walls ?? []).filter((w) => !isOuter(w));
  const taken = new Set(partitions.map((w) => w.id));
  outer.forEach((wall, i) => {
    let id = `w_${i + 1}`;
    let n = i + 1;
    while (taken.has(id)) id = `w_outer_${++n}`;
    wall.id = id;
    taken.add(id);
  });
  const next: Plan = { ...plan, walls: [...outer, ...partitions] };
  const doors = (plan.doors ?? []).filter((d) => hostWall(next, d.wall));
  const windows = (plan.windows ?? []).filter((w) => hostWall(next, w.wall));
  const dropped = (plan.doors ?? []).length - doors.length + (plan.windows ?? []).length - windows.length;
  return { plan: { ...next, doors, windows }, droppedOpenings: dropped };
}

/** Удаление стены вместе с проёмами на ней: без стены проём висит в воздухе. */
export function removeWall(plan: Plan, wallId: string): Plan {
  const on = (segment: Segment) => hostWall(plan, segment)?.id === wallId;
  return {
    ...plan,
    walls: (plan.walls ?? []).filter((w) => w.id !== wallId),
    doors: (plan.doors ?? []).filter((d) => !on(d.wall)),
    windows: (plan.windows ?? []).filter((w) => !on(w.wall)),
  };
}
