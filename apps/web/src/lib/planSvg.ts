/**
 * Чертёж плана отдельным файлом SVG.
 *
 * Экранный холст рисуется классами из таблицы стилей: выгруженный файл должен открываться
 * где угодно, поэтому здесь всё рисуется с явными атрибутами и без внешних зависимостей.
 */

import { equipmentCorners, planBounds, type Equipment, type Plan, type Point } from './plan';
import { formatNumber, outlinePoints, planArea, plural } from './planFacts';
import { buildAddressBook, key } from './addresses';
import { isOuter, offsetOutline, outwardNormal, OUTER_WALL_M, PARTITION_M, hostWall } from './room';

const PAD = 48;
const HEADER = 64;
const MAX_WIDTH = 1600;

const ZONE_FILL: Record<string, string> = {
  waiting: '#dfe7ea',
  queue_accumulator: '#efe3cd',
  back_office: '#e8e6e0',
  staff_room: '#e8e6e0',
};

function escape(text: string): string {
  return text.replace(/[&<>"]/g, (char) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' })[char] ?? char);
}

export function planToSvg(plan: Plan): string {
  const bounds = planBounds(plan);
  const spanX = Math.max(bounds.maxX - bounds.minX, 1);
  const spanY = Math.max(bounds.maxY - bounds.minY, 1);
  const scale = Math.min((MAX_WIDTH - PAD * 2) / spanX, 90);
  const width = Math.round(spanX * scale + PAD * 2);
  const height = Math.round(spanY * scale + PAD * 2 + HEADER);
  const at = ([x, y]: Point): Point => [
    Number(((x - bounds.minX) * scale + PAD).toFixed(1)),
    Number(((bounds.maxY - y) * scale + PAD + HEADER).toFixed(1)),
  ];
  const path = (points: Point[], close = true) =>
    `M${points.map((p) => at(p).join(' ')).join('L')}${close ? 'Z' : ''}`;
  const book = buildAddressBook(plan);
  const outline = outlinePoints(plan);
  const parts: string[] = [];

  parts.push(`<rect width="${width}" height="${height}" fill="#f4f1e8"/>`);
  parts.push(
    `<text x="${PAD}" y="34" font-size="20" font-weight="640" fill="#1c2023">${escape(plan.ops.name ?? 'План отделения')}</text>`,
  );
  const area = planArea(plan);
  const facts = [
    area !== null ? `${formatNumber(area, 1)} м²` : null,
    `${plan.equipment?.length ?? 0} ${plural(plan.equipment?.length ?? 0, 'объект', 'объекта', 'объектов')}`,
    new Date().toLocaleDateString('ru-RU', { day: 'numeric', month: 'long', year: 'numeric' }),
  ].filter(Boolean);
  parts.push(`<text x="${PAD}" y="54" font-size="13" fill="#5c6265">${escape(facts.join(' · '))}</text>`);

  for (const zone of plan.zones ?? []) {
    parts.push(`<path d="${path(zone.polygon)}" fill="${ZONE_FILL[zone.type] ?? '#eeece6'}" stroke="#c9c1ab" stroke-dasharray="6 4"/>`);
  }

  if (outline) {
    parts.push(
      `<path d="${path(outline)}${path(offsetOutline(outline, OUTER_WALL_M))}" fill="#1c2023" fill-rule="evenodd"/>`,
    );
  }
  for (const wall of plan.walls ?? []) {
    if (isOuter(wall) && outline) continue;
    const thickness = isOuter(wall) ? OUTER_WALL_M : PARTITION_M;
    const [nx, ny] = isOuter(wall) ? outwardNormal(plan, wall, outline) : [0, 0];
    const shift = isOuter(wall) ? thickness / 2 : 0;
    const [ax, ay] = at([wall.from[0] + nx * shift, wall.from[1] + ny * shift]);
    const [bx, by] = at([wall.to[0] + nx * shift, wall.to[1] + ny * shift]);
    parts.push(`<line x1="${ax}" y1="${ay}" x2="${bx}" y2="${by}" stroke="#1c2023" stroke-width="${thickness * scale}"/>`);
  }

  for (const opening of [...(plan.doors ?? []), ...(plan.windows ?? [])]) {
    const host = hostWall(plan, opening.wall);
    const outer = host ? isOuter(host) : true;
    const thickness = outer ? OUTER_WALL_M : PARTITION_M;
    const [nx, ny] = host && outer ? outwardNormal(plan, host, outline) : [0, 0];
    const shift = outer ? thickness / 2 : 0;
    const [ax, ay] = at([opening.wall[0] + nx * shift, opening.wall[1] + ny * shift]);
    const [bx, by] = at([opening.wall[2] + nx * shift, opening.wall[3] + ny * shift]);
    const isDoor = (plan.doors ?? []).some((d) => d.id === opening.id);
    parts.push(
      `<line x1="${ax}" y1="${ay}" x2="${bx}" y2="${by}" stroke="#f4f1e8" stroke-width="${thickness * scale + 2}"/>`,
    );
    if (!isDoor) {
      parts.push(`<line x1="${ax}" y1="${ay}" x2="${bx}" y2="${by}" stroke="#1c2023" stroke-width="1.5"/>`);
    }
  }

  for (const item of plan.equipment ?? []) {
    const corners = equipmentCorners(item);
    parts.push(`<path d="${path(corners)}" fill="#fbf9f3" stroke="#41474a" stroke-width="1.5"/>`);
    const [cx, cy] = at(item.position);
    const label = `${book.byId.get(key('equipment', item.id)) ?? ''} ${item.label ?? item.type}`.trim();
    parts.push(
      `<text x="${cx}" y="${cy + 4}" font-size="11" fill="#41474a" text-anchor="middle">${escape(shorten(label, item, scale))}</text>`,
    );
    if (item.operator_position) {
      const [ox, oy] = at(item.operator_position);
      parts.push(`<circle cx="${ox}" cy="${oy}" r="${Math.max(0.18 * scale, 3)}" fill="#b8bfc2"/>`);
    }
  }

  for (const zone of plan.zones ?? []) {
    const centre = zone.polygon.reduce((sum, p) => [sum[0] + p[0] / zone.polygon.length, sum[1] + p[1] / zone.polygon.length], [0, 0] as Point);
    const [zx, zy] = at(centre);
    parts.push(
      `<text x="${zx}" y="${zy}" font-size="12" fill="#5c6265" text-anchor="middle">${escape(zone.label ?? zone.type)}</text>`,
    );
  }

  // Масштабная линейка: чертёж остаётся измеримым и на бумаге.
  const bar = scale >= 60 ? 1 : 2;
  const barY = height - 20;
  parts.push(
    `<path d="M${PAD} ${barY}h${bar * scale}M${PAD} ${barY - 5}v10M${PAD + bar * scale} ${barY - 5}v10" stroke="#41474a" stroke-width="1.5" fill="none"/>`,
    `<text x="${PAD + bar * scale + 8}" y="${barY + 4}" font-size="12" fill="#41474a">${bar} м</text>`,
  );

  return `<svg xmlns="http://www.w3.org/2000/svg" width="${width}" height="${height}" viewBox="0 0 ${width} ${height}" font-family="Golos Text, system-ui, sans-serif">${parts.join('')}</svg>`;
}

/** Подпись, которая не влезает в габарит объекта, сокращается до адреса. */
function shorten(label: string, item: Equipment, scale: number): string {
  const available = Math.min(item.size_m[0], item.size_m[1] * 3) * scale;
  if (label.length * 6 <= available) return label;
  return label.split(' ')[0];
}

export function downloadPlanSvg(plan: Plan): void {
  const name = (plan.ops.name ?? 'план').replace(/[\\/:*?"<>|]+/g, ' ').trim() || 'план';
  const url = URL.createObjectURL(new Blob([planToSvg(plan)], { type: 'image/svg+xml' }));
  const link = document.createElement('a');
  link.href = url;
  link.download = `${name}.svg`;
  document.body.append(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(url);
}
