/**
 * Помещение: грани стен, привязка оборудования и удержание его внутри.
 *
 * В формате плана наружная стена задана своей внутренней гранью: площадь помещения считается
 * по этим линиям, и толщина стены лежит снаружи. Перегородка задана осью и имеет толщину
 * PARTITION_M по обе стороны. Объект касается стены гранью, а не осью, и не может заходить
 * ни в толщу стены, ни за контур помещения.
 */

import type { Equipment, Plan, Point, Wall } from './plan';
import { equipmentCorners } from './plan';
import { outlinePoints } from './planFacts';
import { pointInPolygon, round3 } from './geometry';

export const OUTER_WALL_M = 0.2;
export const PARTITION_M = 0.1;
/** Край ближе этого к стене придвигается вплотную: зазор меньше ладони бесполезен. */
export const WALL_MAGNET_M = 0.2;
/** Соседний объект притягивает слабее: между оборудованием зазор бывает нужен. */
const ITEM_MAGNET_M = 0.12;
const EPS = 1e-3;

export type Axis = 'x' | 'y';

/** Грань, к которой прижимается объект: линия на оси и сторона, где должен стоять объект. */
interface Face {
  axis: Axis;
  /** Координата грани: x для вертикальной, y для горизонтальной. */
  at: number;
  from: number;
  to: number;
  /** +1 — объект по возрастанию координаты от грани, −1 — по убыванию. */
  side: 1 | -1;
  kind: 'outer' | 'partition' | 'item';
}

export function isOuter(wall: Wall): boolean {
  return (wall.type ?? 'outer') === 'outer';
}

/** Нормаль наружной стены, направленная из помещения наружу. */
export function outwardNormal(plan: Plan, wall: Wall, outline = outlinePoints(plan)): Point {
  const [ax, ay] = wall.from;
  const [bx, by] = wall.to;
  const length = Math.hypot(bx - ax, by - ay) || 1;
  let nx = -(by - ay) / length;
  let ny = (bx - ax) / length;
  if (outline) {
    const probe: Point = [(ax + bx) / 2 + nx * 0.05, (ay + by) / 2 + ny * 0.05];
    if (!pointInPolygon(probe, outline)) return [nx, ny];
    nx = -nx;
    ny = -ny;
  }
  return [nx, ny];
}

function axisAligned(wall: Wall): Axis | null {
  if (Math.abs(wall.from[0] - wall.to[0]) < EPS) return 'x';
  if (Math.abs(wall.from[1] - wall.to[1]) < EPS) return 'y';
  return null;
}

function wallFaces(plan: Plan, outline: Point[] | null): Face[] {
  const faces: Face[] = [];
  for (const wall of plan.walls ?? []) {
    const axis = axisAligned(wall);
    if (!axis) continue;
    const along = axis === 'x' ? 1 : 0;
    const from = Math.min(wall.from[along], wall.to[along]);
    const to = Math.max(wall.from[along], wall.to[along]);
    const at = axis === 'x' ? wall.from[0] : wall.from[1];
    if (isOuter(wall)) {
      const normal = outwardNormal(plan, wall, outline);
      const outward = axis === 'x' ? normal[0] : normal[1];
      faces.push({ axis, at, from, to, side: outward > 0 ? -1 : 1, kind: 'outer' });
    } else {
      faces.push({ axis, at: at + PARTITION_M / 2, from, to, side: 1, kind: 'partition' });
      faces.push({ axis, at: at - PARTITION_M / 2, from, to, side: -1, kind: 'partition' });
    }
  }
  return faces;
}

function itemFaces(plan: Plan, skip: string): Face[] {
  const faces: Face[] = [];
  for (const other of plan.equipment ?? []) {
    if (other.id === skip || !isAxisAligned(other)) continue;
    const box = bounds(other);
    faces.push({ axis: 'x', at: box.minX, from: box.minY, to: box.maxY, side: -1, kind: 'item' });
    faces.push({ axis: 'x', at: box.maxX, from: box.minY, to: box.maxY, side: 1, kind: 'item' });
    faces.push({ axis: 'y', at: box.minY, from: box.minX, to: box.maxX, side: -1, kind: 'item' });
    faces.push({ axis: 'y', at: box.maxY, from: box.minX, to: box.maxX, side: 1, kind: 'item' });
  }
  return faces;
}

function isAxisAligned(item: Equipment): boolean {
  const rotation = (((item.rotation_deg ?? 0) % 90) + 90) % 90;
  return rotation < 0.01 || rotation > 89.99;
}

function bounds(item: Equipment) {
  const corners = equipmentCorners(item);
  const xs = corners.map((p) => p[0]);
  const ys = corners.map((p) => p[1]);
  return { minX: Math.min(...xs), maxX: Math.max(...xs), minY: Math.min(...ys), maxY: Math.max(...ys) };
}

/** Направляющая: линия, по которой объект выровнялся с соседом. */
export interface Guide {
  axis: Axis;
  /** Координата линии по своей оси. */
  at: number;
  /** Отрезок по другой оси: линия рисуется от одного объекта до другого. */
  from: number;
  to: number;
}

/**
 * Выравнивание объекта по соседям: одна линия середин или краёв.
 *
 * В отделении вещи стоят рядами: три окна обслуживания в линию, скамьи одна за другой,
 * стеллажи вдоль стены. Прижать объект к грани соседа умеет магнит, но встать с соседом на
 * одну линию — нет, а глазом полсантиметра разницы не поймать. Здесь ищется ближайшее
 * совпадение середины или края с серединой или краем соседа; найденное показывается линией,
 * чтобы было видно, по чему объект выровнялся.
 */
export function alignToNeighbours(
  plan: Plan,
  item: Equipment,
  tolerance: number,
): { position: Point; guides: Guide[] } {
  if (!isAxisAligned(item) || tolerance <= 0) return { position: item.position, guides: [] };
  const box = bounds(item);
  // Середина идёт первой: при равном расстоянии ряд выравнивается по осевой, а не по краю.
  const self = {
    x: [(box.minX + box.maxX) / 2, box.minX, box.maxX],
    y: [(box.minY + box.maxY) / 2, box.minY, box.maxY],
  };
  const best: Record<Axis, { shift: number; at: number; distance: number; from: number; to: number } | null> = {
    x: null,
    y: null,
  };
  for (const other of plan.equipment ?? []) {
    if (other.id === item.id || !isAxisAligned(other)) continue;
    const box2 = bounds(other);
    const lines = {
      x: [(box2.minX + box2.maxX) / 2, box2.minX, box2.maxX],
      y: [(box2.minY + box2.maxY) / 2, box2.minY, box2.maxY],
    };
    for (const axis of ['x', 'y'] as Axis[]) {
      for (const mine of self[axis]) {
        for (const theirs of lines[axis]) {
          const distance = Math.abs(theirs - mine);
          if (distance > tolerance) continue;
          const current = best[axis];
          if (current && current.distance <= distance) continue;
          // Линия тянется от дальнего края соседа до дальнего края объекта — видно, с чем выровнялись.
          const cross = axis === 'x' ? ['minY', 'maxY'] : ['minX', 'maxX'];
          const from = Math.min(box[cross[0] as 'minY'], box2[cross[0] as 'minY']);
          const to = Math.max(box[cross[1] as 'maxY'], box2[cross[1] as 'maxY']);
          best[axis] = { shift: theirs - mine, at: theirs, distance, from, to };
        }
      }
    }
  }
  const dx = best.x?.shift ?? 0;
  const dy = best.y?.shift ?? 0;
  const guides: Guide[] = [];
  if (best.x) guides.push({ axis: 'x', at: best.x.at, from: best.x.from + dy, to: best.x.to + dy });
  if (best.y) guides.push({ axis: 'y', at: best.y.at, from: best.y.from + dx, to: best.y.to + dx });
  return { position: [round3(item.position[0] + dx), round3(item.position[1] + dy)], guides };
}

/**
 * Сдвиг вдоль оси, прижимающий объект к ближайшей грани. Учитываются и небольшие заезды
 * в стену: их тоже надо убрать, а не только зазоры.
 */
function magnetShift(box: ReturnType<typeof bounds>, faces: Face[], axis: Axis): number {
  const lo = axis === 'x' ? box.minX : box.minY;
  const hi = axis === 'x' ? box.maxX : box.maxY;
  const crossLo = axis === 'x' ? box.minY : box.minX;
  const crossHi = axis === 'x' ? box.maxY : box.maxX;
  let best = 0;
  let bestDistance = Infinity;
  for (const face of faces) {
    if (face.axis !== axis) continue;
    if (Math.min(crossHi, face.to) - Math.max(crossLo, face.from) <= EPS) continue;
    const threshold = face.kind === 'item' ? ITEM_MAGNET_M : WALL_MAGNET_M;
    // Объект должен оказаться по свою сторону грани: для side = +1 его нижний край на грани.
    const shift = face.side === 1 ? face.at - lo : face.at - hi;
    const distance = Math.abs(shift);
    if (distance > threshold || distance >= bestDistance) continue;
    // Прижимать можно только тот объект, центр которого уже по нужную сторону грани.
    const center = (lo + hi) / 2;
    if (face.side === 1 ? center < face.at : center > face.at) continue;
    best = shift;
    bestDistance = distance;
  }
  return best;
}

/** Проекции двух выпуклых многоугольников перекрываются по всем осям — они пересекаются. */
function overlaps(a: Point[], b: Point[], tolerance = EPS): boolean {
  for (const polygon of [a, b]) {
    for (let i = 0; i < polygon.length; i += 1) {
      const [x1, y1] = polygon[i];
      const [x2, y2] = polygon[(i + 1) % polygon.length];
      const nx = -(y2 - y1);
      const ny = x2 - x1;
      const length = Math.hypot(nx, ny) || 1;
      const project = (points: Point[]) => points.map(([x, y]) => (x * nx + y * ny) / length);
      const pa = project(a);
      const pb = project(b);
      const overlap = Math.min(Math.max(...pa), Math.max(...pb)) - Math.max(Math.min(...pa), Math.min(...pb));
      if (overlap <= tolerance) return false;
    }
  }
  return true;
}

function partitionBand(wall: Wall): Point[] {
  const [ax, ay] = wall.from;
  const [bx, by] = wall.to;
  const length = Math.hypot(bx - ax, by - ay) || 1;
  const nx = (-(by - ay) / length) * (PARTITION_M / 2);
  const ny = ((bx - ax) / length) * (PARTITION_M / 2);
  return [
    [ax + nx, ay + ny],
    [bx + nx, by + ny],
    [bx - nx, by - ny],
    [ax - nx, ay - ny],
  ];
}

/** Объект целиком в помещении и не заходит в перегородки. */
export function fitsRoom(plan: Plan, item: Equipment, outline = outlinePoints(plan)): boolean {
  const corners = equipmentCorners(item);
  if (outline) {
    const [cx, cy] = item.position;
    // Угол на самой стене допустим: проверяется точка, чуть смещённая к центру объекта.
    const inside = corners.every(([x, y]) => {
      const length = Math.hypot(cx - x, cy - y) || 1;
      return pointInPolygon([x + ((cx - x) / length) * EPS * 2, y + ((cy - y) / length) * EPS * 2], outline);
    });
    if (!inside) return false;
    // Вогнутый контур: ребро объекта может пересечь угол помещения, хотя все вершины внутри.
    const samples = corners.flatMap((p, i) => {
      const q = corners[(i + 1) % corners.length];
      return [0.25, 0.5, 0.75].map((t) => {
        const x = p[0] + (q[0] - p[0]) * t;
        const y = p[1] + (q[1] - p[1]) * t;
        const length = Math.hypot(cx - x, cy - y) || 1;
        return [x + ((cx - x) / length) * EPS * 2, y + ((cy - y) / length) * EPS * 2] as Point;
      });
    });
    if (!samples.every((point) => pointInPolygon(point, outline))) return false;
  }
  return (plan.walls ?? []).filter((w) => !isOuter(w)).every((wall) => !overlaps(corners, partitionBand(wall)));
}

/**
 * Новое положение объекта: привязанное к стенам и соседям, внутри помещения. Если объект
 * туда не помещается, остаётся прежнее положение — объект упирается в стену, а не проходит
 * сквозь неё.
 */
export function placeInRoom(plan: Plan, item: Equipment, previous: Point | null, floorObject = true): Point {
  const outline = outlinePoints(plan);
  let position = item.position;
  if (isAxisAligned(item)) {
    const faces = [...wallFaces(plan, outline), ...(floorObject ? itemFaces(plan, item.id) : [])];
    const box = bounds(item);
    const dx = magnetShift(box, faces, 'x');
    const dy = magnetShift(box, faces, 'y');
    position = [round3(position[0] + dx), round3(position[1] + dy)];
  }
  if (!floorObject) return position;
  const candidate = { ...item, position };
  if (fitsRoom(plan, candidate, outline)) return position;
  if (fitsRoom(plan, item, outline)) return item.position;
  // Упор в стену по одной оси: объект скользит вдоль стены, а не застревает.
  if (previous) {
    const alongX: Equipment = { ...item, position: [position[0], previous[1]] };
    if (fitsRoom(plan, alongX, outline)) return alongX.position;
    const alongY: Equipment = { ...item, position: [previous[0], position[1]] };
    if (fitsRoom(plan, alongY, outline)) return alongY.position;
    return previous;
  }
  return position;
}

/** Стена, на которой лежит проём: отрезок проёма коллинеарен ей и не выходит за её концы. */
export function hostWall(plan: Plan, segment: [number, number, number, number]): Wall | null {
  const [px, py, qx, qy] = segment;
  let best: Wall | null = null;
  let bestDistance = Infinity;
  for (const wall of plan.walls ?? []) {
    const [ax, ay] = wall.from;
    const [bx, by] = wall.to;
    const length = Math.hypot(bx - ax, by - ay);
    if (length < EPS) continue;
    const distance = (x: number, y: number) => Math.abs((bx - ax) * (ay - y) - (ax - x) * (by - ay)) / length;
    const along = (x: number, y: number) => ((x - ax) * (bx - ax) + (y - ay) * (by - ay)) / (length * length);
    const d = Math.max(distance(px, py), distance(qx, qy));
    const t1 = along(px, py);
    const t2 = along(qx, qy);
    if (d < 0.05 && Math.min(t1, t2) > -0.02 && Math.max(t1, t2) < 1.02 && d < bestDistance) {
      best = wall;
      bestDistance = d;
    }
  }
  return best;
}

/**
 * Контур, смещённый наружу на distance, с прямыми стыками в углах: вершина — пересечение
 * смещённых соседних рёбер. Так наружная стена рисуется одним кольцом без зубцов на
 * вогнутых углах.
 */
export function offsetOutline(points: Point[], distance: number): Point[] {
  const n = points.length;
  let signed = 0;
  for (let i = 0; i < n; i += 1) {
    const [x1, y1] = points[i];
    const [x2, y2] = points[(i + 1) % n];
    signed += x1 * y2 - x2 * y1;
  }
  // Для обхода против часовой стрелки наружу смотрит правая нормаль ребра.
  const sign = signed > 0 ? 1 : -1;
  const offsetEdge = (i: number) => {
    const a = points[i];
    const b = points[(i + 1) % n];
    const length = Math.hypot(b[0] - a[0], b[1] - a[1]) || 1;
    const nx = ((b[1] - a[1]) / length) * sign * distance;
    const ny = (-(b[0] - a[0]) / length) * sign * distance;
    return { a: [a[0] + nx, a[1] + ny] as Point, b: [b[0] + nx, b[1] + ny] as Point };
  };
  return points.map((_, i) => {
    const prev = offsetEdge((i - 1 + n) % n);
    const next = offsetEdge(i);
    const d1: Point = [prev.b[0] - prev.a[0], prev.b[1] - prev.a[1]];
    const d2: Point = [next.b[0] - next.a[0], next.b[1] - next.a[1]];
    const cross = d1[0] * d2[1] - d1[1] * d2[0];
    if (Math.abs(cross) < 1e-9) return next.a;
    const t = ((next.a[0] - prev.a[0]) * d2[1] - (next.a[1] - prev.a[1]) * d2[0]) / cross;
    return [prev.a[0] + d1[0] * t, prev.a[1] + d1[1] * t];
  });
}
