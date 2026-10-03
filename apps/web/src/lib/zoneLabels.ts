/**
 * Где подписать зону.
 *
 * Центр масс подходит только выпуклым зонам: у Г-образного зала он попадает на соседнюю зону, и
 * подписи ложатся друг на друга. Поэтому точка подбирается перебором по сетке внутри зоны: не на
 * вложенной зоне, не на оборудовании и подальше от уже поставленных подписей. Зоны идут от меньших
 * к большим — вложенные получают лучшее место первыми, а объемлющая подписывается там, где
 * остался простор.
 */

import type { Plan, Point, Zone } from './plan';
import { equipmentCorners } from './plan';
import { pointInPolygon, polygonCentroid } from './geometry';

/** Средняя ширина знака подписи при кегле 12 px и высота строки — для оценки её прямоугольника. */
const CHAR_PX = 6.6;
const LINE_PX = 15;
/** Подпись длиннее этого переносится в две строки по ближайшему к середине пробелу. */
const WRAP_CHARS = 16;

/** Подпись зоны в одну или две строки. */
export function labelLines(text: string): string[] {
  if (text.length <= WRAP_CHARS || !text.includes(' ')) return [text];
  const middle = text.length / 2;
  let cut = -1;
  for (let i = 0; i < text.length; i += 1) {
    if (text[i] === ' ' && (cut < 0 || Math.abs(i - middle) < Math.abs(cut - middle))) cut = i;
  }
  return [text.slice(0, cut), text.slice(cut + 1)];
}

/** Полуширина и полувысота подписи в метрах при данном масштабе (пикселей на метр). */
export function labelHalfSize(text: string, pxPerM: number): [number, number] {
  const lines = labelLines(text);
  const widest = Math.max(...lines.map((line) => line.length));
  return [(widest * CHAR_PX) / 2 / pxPerM, (lines.length * LINE_PX) / 2 / pxPerM];
}
/** Дальше этого расстояния до объекта подпись уже не мешает — больший зазор не нужен. */
const ENOUGH_CLEARANCE_M = 1.0;
/** Подписи ближе этого расстояния друг к другу наезжают при обычном масштабе. */
const LABEL_SPACING_M = 2.2;

function area(points: Point[]): number {
  let sum = 0;
  for (let i = 0; i < points.length; i += 1) {
    const [x1, y1] = points[i];
    const [x2, y2] = points[(i + 1) % points.length];
    sum += x1 * y2 - x2 * y1;
  }
  return Math.abs(sum) / 2;
}

function segmentDistance([px, py]: Point, [ax, ay]: Point, [bx, by]: Point): number {
  const dx = bx - ax;
  const dy = by - ay;
  const length = dx * dx + dy * dy;
  const t = length === 0 ? 0 : Math.max(0, Math.min(1, ((px - ax) * dx + (py - ay) * dy) / length));
  return Math.hypot(px - (ax + t * dx), py - (ay + t * dy));
}

function distanceToPolygon(point: Point, polygon: Point[]): number {
  if (pointInPolygon(point, polygon)) return 0;
  let best = Infinity;
  for (let i = 0; i < polygon.length; i += 1) {
    best = Math.min(best, segmentDistance(point, polygon[i], polygon[(i + 1) % polygon.length]));
  }
  return best;
}

function edgeDistance(point: Point, polygon: Point[]): number {
  let best = Infinity;
  for (let i = 0; i < polygon.length; i += 1) {
    best = Math.min(best, segmentDistance(point, polygon[i], polygon[(i + 1) % polygon.length]));
  }
  return best;
}

interface Obstacle {
  corners: Point[];
  minX: number;
  minY: number;
  maxX: number;
  maxY: number;
}

/** Корзины по метру: подписи важны только соседние объекты, а не все сотни на большом плане. */
const BUCKET_M = 1.0;
/** Сначала грубый перебор, затем уточнение вокруг лучших мест. */
const COARSE_M = 0.5;
const FINE_M = 0.125;
const REFINE_TOP = 3;

function bucketIndex(obstacles: Obstacle[]) {
  const buckets = new Map<string, Obstacle[]>();
  const cell = (value: number) => Math.floor(value / BUCKET_M);
  for (const obstacle of obstacles) {
    for (let ix = cell(obstacle.minX); ix <= cell(obstacle.maxX); ix += 1) {
      for (let iy = cell(obstacle.minY); iy <= cell(obstacle.maxY); iy += 1) {
        const key = `${ix}:${iy}`;
        const list = buckets.get(key);
        if (list) list.push(obstacle);
        else buckets.set(key, [obstacle]);
      }
    }
  }
  return (minX: number, minY: number, maxX: number, maxY: number): Obstacle[] => {
    const found = new Set<Obstacle>();
    for (let ix = cell(minX); ix <= cell(maxX); ix += 1) {
      for (let iy = cell(minY); iy <= cell(maxY); iy += 1) {
        for (const obstacle of buckets.get(`${ix}:${iy}`) ?? []) found.add(obstacle);
      }
    }
    return [...found];
  };
}

/**
 * Точки подписей зон по идентификатору зоны, в метрах плана. С масштабом (пикселей на метр)
 * подпись учитывается как прямоугольник: важно, чтобы на объект не легли её края, а не только центр.
 */
export function zoneLabelPoints(plan: Plan, pxPerM?: number): Map<string, Point> {
  const zones = [...(plan.zones ?? [])].filter((zone) => zone.polygon.length >= 3);
  const obstacles: Obstacle[] = (plan.equipment ?? []).map((item) => {
    const corners = equipmentCorners(item);
    const xs = corners.map((p) => p[0]);
    const ys = corners.map((p) => p[1]);
    return { corners, minX: Math.min(...xs), minY: Math.min(...ys), maxX: Math.max(...xs), maxY: Math.max(...ys) };
  });
  const near = bucketIndex(obstacles);
  const placed: Point[] = [];
  const result = new Map<string, Point>();

  const ordered = zones.sort((a, b) => area(a.polygon) - area(b.polygon));
  for (const zone of ordered) {
    const own = area(zone.polygon);
    // Вложенные зоны — меньшие по площади, чей центр лежит внутри этой.
    const children: Zone[] = ordered.filter(
      (other) => other.id !== zone.id && area(other.polygon) < own && pointInPolygon(polygonCentroid(other.polygon), zone.polygon),
    );
    const centre = polygonCentroid(zone.polygon);
    const [halfW, halfH] = pxPerM ? labelHalfSize(zone.label ?? zone.type, pxPerM) : [0, 0];
    // Точки прямоугольника подписи относительно её центра: углы, середины сторон и центр.
    const probes: Point[] = pxPerM
      ? [[0, 0], [-halfW, 0], [halfW, 0], [-halfW, -halfH], [halfW, -halfH], [-halfW, halfH], [halfW, halfH], [0, -halfH], [0, halfH]]
      : [[0, 0]];
    const reach = ENOUGH_CLEARANCE_M;

    const score = (x: number, y: number): number | null => {
      const point: Point = [x, y];
      if (!pointInPolygon(point, zone.polygon)) return null;
      if (children.some((child) => pointInPolygon(point, child.polygon))) return null;
      const nearby = near(x - halfW - reach, y - halfH - reach, x + halfW + reach, y + halfH + reach);
      let clearance = ENOUGH_CLEARANCE_M;
      let outside = 0;
      for (const [dx, dy] of probes) {
        const probe: Point = [x + dx, y + dy];
        for (const obstacle of nearby) clearance = Math.min(clearance, distanceToPolygon(probe, obstacle.corners));
        if (!pointInPolygon(probe, zone.polygon)) outside += 1;
      }
      const spacing = Math.min(LABEL_SPACING_M, ...placed.map((other) => Math.hypot(other[0] - x, other[1] - y)));
      const margin = Math.min(0.8, edgeDistance(point, zone.polygon));
      const drift = Math.hypot(x - centre[0], y - centre[1]);
      // Край подписи за границей зоны хуже, чем зазор поменьше: она начинает подписывать соседнюю.
      return clearance * 3 + spacing * 2 + margin - drift * 0.08 - outside * 0.6;
    };

    const xs = zone.polygon.map((p) => p[0]);
    const ys = zone.polygon.map((p) => p[1]);
    const coarse: { point: Point; value: number }[] = [];
    for (let x = Math.min(...xs) + COARSE_M / 2; x < Math.max(...xs); x += COARSE_M) {
      for (let y = Math.min(...ys) + COARSE_M / 2; y < Math.max(...ys); y += COARSE_M) {
        const value = score(x, y);
        if (value !== null) coarse.push({ point: [x, y], value });
      }
    }
    coarse.sort((a, b) => b.value - a.value);
    let best: Point = centre;
    let bestScore = -Infinity;
    // У маленькой зоны грубая сетка может не попасть внутрь: тогда уточнение идёт вокруг центра.
    const seeds = coarse.length ? coarse.slice(0, REFINE_TOP).map((c) => c.point) : [centre];
    for (const [sx, sy] of seeds) {
      for (let x = sx - COARSE_M; x <= sx + COARSE_M + 1e-9; x += FINE_M) {
        for (let y = sy - COARSE_M; y <= sy + COARSE_M + 1e-9; y += FINE_M) {
          const value = score(x, y);
          if (value !== null && value > bestScore) {
            bestScore = value;
            best = [x, y];
          }
        }
      }
    }
    result.set(zone.id, best);
    placed.push(best);
  }
  return result;
}
