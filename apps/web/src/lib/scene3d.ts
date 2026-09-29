/**
 * План — в объёмную сцену.
 *
 * Модуль намеренно ничего не знает о three.js: он переводит план в список прямоугольных призм,
 * лент маршрутов и фигур, а рисование остаётся делом холста. Так геометрию можно проверить
 * тестами, а запасной упрощённый режим рисует ровно то же самое, только дешевле.
 *
 * Высоты не выдуманы на месте: они приходят из справочника типов, который редактируется вместе
 * с планом, а у конкретного объекта может быть свой `height_m`. Поэтому объём соответствует
 * плану, а не представлениям о том, как выглядит почта.
 */

import type { Equipment, EquipmentTypeInfo, Finding, Plan, Point, ValidationReport } from './plan';
import { equipmentCorners } from './plan';
import { outlinePoints } from './planFacts';
import { OUTER_WALL_M, PARTITION_M, isOuter, outwardNormal } from './room';

/** Высота дверного проёма и подоконника: над ними стена продолжается. */
export const DOOR_HEIGHT_M = 2.1;
export const SILL_HEIGHT_M = 0.9;
const DEFAULT_CEILING_M = 3.0;
const DEFAULT_ITEM_HEIGHT_M = 1.0;
/** Настенный объект без указанной высоты крепления висит на уровне глаз. */
const DEFAULT_MOUNT_M = 1.4;
const EPS = 1e-6;

export type PrismKind = 'wall' | 'partition' | 'equipment' | 'operator' | 'figure' | 'glazing';

/** Всё в сцене — прямоугольная призма: так и упрощённый режим, и подробный рисуют одинаково. */
export interface Prism {
  id: string;
  label: string;
  kind: PrismKind;
  /** Центр основания в метрах плана. */
  centre: Point;
  /** Габариты по местным осям до поворота. */
  size: [number, number];
  rotationDeg: number;
  /** Низ над полом и собственная высота. */
  base: number;
  height: number;
  /** Участвует в ошибке проверки: красный цвет и метка сверху. */
  violation: boolean;
  /** Участвует только в предупреждении: янтарный цвет без метки. */
  caution: boolean;
  typeCode?: string;
}

/** Лента типового маршрута посетителя по полу. */
export interface Ribbon {
  label: string;
  share: number;
  points: Point[];
}

/** Условная фигура для чувства масштаба: рост человека и габарит кресла-коляски. */
export interface Figure {
  id: string;
  label: string;
  centre: Point;
  radius: number;
  height: number;
}

export interface Scene {
  floor: Point[];
  prisms: Prism[];
  ribbons: Ribbon[];
  figures: Figure[];
  ceiling: number;
  /** Центр и радиус сцены — по ним ставится камера. */
  centre: Point;
  radius: number;
  violations: number;
  cautions: number;
}

function objectsBySeverity(report: ValidationReport | null, severity: 'error' | 'warning'): Set<string> {
  const ids = new Set<string>();
  for (const finding of report?.findings ?? []) {
    if (finding.severity !== severity) continue;
    for (const id of finding.objects) ids.add(id);
  }
  return ids;
}

/** Нарушения, в которых участвует объект, — для подписи под курсором. */
export function findingsFor(report: ValidationReport | null, id: string): Finding[] {
  return (report?.findings ?? []).filter((finding) => finding.objects.includes(id));
}

function heightOf(item: Equipment, type: EquipmentTypeInfo | undefined): [number, number] {
  const height = item.height_m ?? type?.height_m ?? DEFAULT_ITEM_HEIGHT_M;
  const floor = type?.floor_object ?? true;
  const mount = type?.mount_height_m ?? (floor ? 0 : DEFAULT_MOUNT_M);
  return [Math.max(0.05, height), Math.max(0, mount)];
}

/** Отрезки проёмов, приходящиеся на стену: по ним стена разрезается. */
function openingsOn(plan: Plan, from: Point, to: Point): { at: number; size: number; kind: 'door' | 'window' }[] {
  const dx = to[0] - from[0];
  const dy = to[1] - from[1];
  const length = Math.hypot(dx, dy);
  if (length < EPS) return [];
  const ux = dx / length;
  const uy = dy / length;
  const found: { at: number; size: number; kind: 'door' | 'window' }[] = [];
  const consider = (segment: [number, number, number, number], kind: 'door' | 'window') => {
    const ends: number[] = [];
    for (const [x, y] of [
      [segment[0], segment[1]],
      [segment[2], segment[3]],
    ] as Point[]) {
      const along = (x - from[0]) * ux + (y - from[1]) * uy;
      const away = Math.abs((x - from[0]) * -uy + (y - from[1]) * ux);
      // Проём принадлежит стене, только если лежит на её линии: иначе это проём соседней стены.
      if (away > 0.25) return;
      ends.push(along);
    }
    const start = Math.max(0, Math.min(ends[0], ends[1]));
    const end = Math.min(length, Math.max(ends[0], ends[1]));
    if (end - start > 0.05) found.push({ at: start, size: end - start, kind });
  };
  for (const door of plan.doors ?? []) consider(door.wall, 'door');
  for (const window of plan.windows ?? []) consider(window.wall, 'window');
  return found.sort((a, b) => a.at - b.at);
}

function wallPrisms(plan: Plan, ceiling: number): Prism[] {
  const outline = outlinePoints(plan);
  const prisms: Prism[] = [];
  for (const wall of plan.walls ?? []) {
    const outer = isOuter(wall);
    const thickness = outer ? OUTER_WALL_M : PARTITION_M * 2;
    const [ax, ay] = wall.from;
    const [bx, by] = wall.to;
    const length = Math.hypot(bx - ax, by - ay);
    if (length < EPS) continue;
    const ux = (bx - ax) / length;
    const uy = (by - ay) / length;
    const angle = (Math.atan2(uy, ux) * 180) / Math.PI;
    // Наружная стена задана внутренней гранью: её толщина уходит наружу помещения.
    const normal = outer && outline ? outwardNormal(plan, wall, outline) : ([0, 0] as Point);
    const shift = outer ? thickness / 2 : 0;

    const piece = (at: number, size: number, base: number, height: number, tag: string, kind?: PrismKind) => {
      if (size <= 0.02 || height <= 0.02) return;
      const mid = at + size / 2;
      prisms.push({
        id: `${wall.id}:${tag}`,
        label: kind === 'glazing' ? 'Остекление' : (wall.label ?? (outer ? 'Наружная стена' : 'Перегородка')),
        kind: kind ?? (outer ? 'wall' : 'partition'),
        centre: [ax + ux * mid + normal[0] * shift, ay + uy * mid + normal[1] * shift],
        size: [size, kind === 'glazing' ? Math.min(thickness, 0.04) : thickness],
        rotationDeg: angle,
        base,
        height,
        violation: false,
        caution: false,
      });
    };

    const openings = openingsOn(plan, wall.from, wall.to);
    let cursor = 0;
    openings.forEach((opening, index) => {
      piece(cursor, opening.at - cursor, 0, ceiling, `s${index}`);
      if (opening.kind === 'door') {
        // Над дверью стена продолжается до потолка — перемычка.
        piece(opening.at, opening.size, DOOR_HEIGHT_M, Math.max(0, ceiling - DOOR_HEIGHT_M), `d${index}`);
      } else {
        piece(opening.at, opening.size, 0, SILL_HEIGHT_M, `p${index}`);
        // Сам проём — стекло: без него окно выглядит дырой в стене.
        piece(opening.at, opening.size, SILL_HEIGHT_M, DOOR_HEIGHT_M - SILL_HEIGHT_M, `g${index}`, 'glazing');
        piece(opening.at, opening.size, DOOR_HEIGHT_M, Math.max(0, ceiling - DOOR_HEIGHT_M), `w${index}`);
      }
      cursor = opening.at + opening.size;
    });
    piece(cursor, length - cursor, 0, ceiling, 'e');
  }
  return prisms;
}

function entrancePoint(plan: Plan): Point | null {
  const doors = plan.doors ?? [];
  const main = doors.find((door) => door.id === plan.flow_hints?.main_entrance)
    ?? doors.find((door) => (door.type ?? 'entrance') === 'entrance')
    ?? doors[0];
  if (!main) return null;
  return [(main.wall[0] + main.wall[2]) / 2, (main.wall[1] + main.wall[3]) / 2];
}

/** Фигуры ставятся у входа, сдвинутые внутрь помещения, и не спорят с оборудованием. */
function figuresAt(plan: Plan, entrance: Point | null): Figure[] {
  if (!entrance) return [];
  const outline = outlinePoints(plan);
  if (!outline) return [];
  const centre = outline.reduce<Point>((sum, p) => [sum[0] + p[0] / outline.length, sum[1] + p[1] / outline.length], [0, 0]);
  const dx = centre[0] - entrance[0];
  const dy = centre[1] - entrance[1];
  const length = Math.hypot(dx, dy) || 1;
  const inward: Point = [dx / length, dy / length];
  const side: Point = [-inward[1], inward[0]];
  const at = (forward: number, across: number): Point => [
    entrance[0] + inward[0] * forward + side[0] * across,
    entrance[1] + inward[1] * forward + side[1] * across,
  ];
  return [
    { id: 'figure:person', label: 'Посетитель, рост 1,7 м', centre: at(1.0, -0.45), radius: 0.22, height: 1.7 },
    { id: 'figure:wheelchair', label: 'Кресло-коляска, габарит 0,9 м', centre: at(1.0, 0.55), radius: 0.45, height: 1.35 },
  ];
}

/**
 * Сцена по плану: контур пола, стены с проёмами, оборудование, маршруты и фигуры.
 *
 * Отчёт проверки необязателен: без него сцена та же, просто никто не подсвечен.
 */
export function buildScene(
  plan: Plan,
  types: Record<string, EquipmentTypeInfo> | Map<string, EquipmentTypeInfo>,
  report: ValidationReport | null = null,
): Scene {
  const lookup = (code: string): EquipmentTypeInfo | undefined =>
    types instanceof Map ? types.get(code) : types[code];
  const ceiling = plan.ops.ceiling_height_m ?? DEFAULT_CEILING_M;
  const guilty = objectsBySeverity(report, 'error');
  const doubtful = objectsBySeverity(report, 'warning');
  const floor = outlinePoints(plan) ?? [];

  const prisms = wallPrisms(plan, ceiling);
  for (const item of plan.equipment ?? []) {
    const type = lookup(item.type);
    const [height, base] = heightOf(item, type);
    prisms.push({
      id: item.id,
      label: item.label ?? type?.label ?? item.type,
      kind: 'equipment',
      centre: item.position,
      size: item.size_m,
      rotationDeg: item.rotation_deg ?? 0,
      base,
      height,
      violation: guilty.has(item.id),
      caution: !guilty.has(item.id) && doubtful.has(item.id),
      typeCode: item.type,
    });
    if (item.operator_position) {
      prisms.push({
        id: `${item.id}:operator`,
        label: `Место сотрудника — ${item.label ?? item.type}`,
        kind: 'operator',
        centre: item.operator_position,
        size: [0.5, 0.5],
        rotationDeg: item.rotation_deg ?? 0,
        base: 0,
        height: 0.45,
        violation: guilty.has(item.id),
        caution: !guilty.has(item.id) && doubtful.has(item.id),
      });
    }
  }

  const ribbons: Ribbon[] = (plan.flow_hints?.typical_paths ?? [])
    .filter((path) => path.waypoints.length >= 2)
    .map((path) => ({
      label: path.label ?? 'Маршрут посетителя',
      share: path.frequency_share ?? 0,
      points: path.waypoints,
    }));

  const points: Point[] = floor.length ? floor : (plan.equipment ?? []).flatMap((item) => equipmentCorners(item));
  const xs = points.map((p) => p[0]);
  const ys = points.map((p) => p[1]);
  const minX = xs.length ? Math.min(...xs) : 0;
  const maxX = xs.length ? Math.max(...xs) : 10;
  const minY = ys.length ? Math.min(...ys) : 0;
  const maxY = ys.length ? Math.max(...ys) : 8;

  return {
    floor,
    prisms,
    ribbons,
    figures: figuresAt(plan, entrancePoint(plan)),
    ceiling,
    centre: [(minX + maxX) / 2, (minY + maxY) / 2],
    radius: Math.max(2, Math.hypot(maxX - minX, maxY - minY) / 2),
    violations: prisms.filter((prism) => prism.violation && prism.kind === 'equipment').length,
    cautions: prisms.filter((prism) => prism.caution && prism.kind === 'equipment').length,
  };
}
