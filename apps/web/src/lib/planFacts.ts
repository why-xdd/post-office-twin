import type { Plan } from './plan';

/** Площадь многоугольника по формуле Гаусса, м². */
export function polygonArea(points: [number, number][]): number {
  let sum = 0;
  for (let i = 0; i < points.length; i += 1) {
    const [x1, y1] = points[i];
    const [x2, y2] = points[(i + 1) % points.length];
    sum += x1 * y2 - x2 * y1;
  }
  return Math.abs(sum) / 2;
}

/** Контур помещения по наружным стенам, если они образуют цепочку. */
export function outlinePoints(plan: Plan): [number, number][] | null {
  const outer = (plan.walls ?? []).filter((w) => (w.type ?? 'outer') === 'outer');
  if (outer.length < 3) return null;
  const points: [number, number][] = [outer[0].from];
  let current = outer[0].to;
  const remaining = outer.slice(1);
  const same = (a: [number, number], b: [number, number]) =>
    Math.abs(a[0] - b[0]) < 1e-6 && Math.abs(a[1] - b[1]) < 1e-6;
  while (remaining.length > 0) {
    const index = remaining.findIndex((w) => same(w.from, current) || same(w.to, current));
    if (index === -1) return null;
    const [wall] = remaining.splice(index, 1);
    points.push(current);
    current = same(wall.from, current) ? wall.to : wall.from;
  }
  return same(current, points[0]) ? points : null;
}

export function planArea(plan: Plan): number | null {
  const outline = outlinePoints(plan);
  if (outline) return polygonArea(outline);
  return plan.ops.total_area_sqm ?? null;
}

const numberFormat = new Intl.NumberFormat('ru-RU', { maximumFractionDigits: 2 });

export function formatNumber(value: number | null | undefined, fraction = 2): string {
  if (value === null || value === undefined || Number.isNaN(value)) return '—';
  return fraction === 2 ? numberFormat.format(value) : value.toLocaleString('ru-RU', { maximumFractionDigits: fraction });
}

/** «3 объекта», «12 объектов», «1 объект». */
export function plural(count: number, one: string, few: string, many: string): string {
  const mod10 = count % 10;
  const mod100 = count % 100;
  if (mod10 === 1 && mod100 !== 11) return one;
  if (mod10 >= 2 && mod10 <= 4 && (mod100 < 12 || mod100 > 14)) return few;
  return many;
}

/** Пустое отделение с прямоугольным контуром — стартовая точка нового плана. */
export function blankPlan(width = 12, height = 8): Plan {
  const id = `ops_${Date.now().toString(36)}`;
  return {
    ops: {
      id,
      name: 'Новое отделение',
      scale: '1 unit = 1 meter',
      bbox: { width_m: width, height_m: height },
      ceiling_height_m: 3,
    },
    walls: [
      { id: 'w_s', from: [0, 0], to: [width, 0], type: 'outer' },
      { id: 'w_e', from: [width, 0], to: [width, height], type: 'outer' },
      { id: 'w_n', from: [width, height], to: [0, height], type: 'outer' },
      { id: 'w_w', from: [0, height], to: [0, 0], type: 'outer' },
    ],
    doors: [
      { id: 'd_main', wall: [1, height, 2.5, height], type: 'entrance', width_m: 1.5, label: 'Главный вход' },
    ],
    windows: [],
    zones: [],
    equipment: [],
    flow_hints: { main_entrance: 'd_main', typical_paths: [] },
  };
}
