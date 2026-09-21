/**
 * Миниатюра плана с метками нарушений — для первого экрана. Только рисунок: без масштаба,
 * выделения и подписей, чтобы с первого взгляда было видно, что именно делает сервис.
 */

import type { Plan, Point, ValidationReport } from '../lib/plan';
import { equipmentCorners, planBounds } from '../lib/plan';
import { formatNumber, outlinePoints } from '../lib/planFacts';
import { hostWall, isOuter, offsetOutline, outwardNormal } from '../lib/room';
import './plan-thumb.css';

interface PlanThumbProps {
  plan: Plan;
  report: ValidationReport | null;
  /** Подписать ширину и глубину помещения — для мастера создания. */
  showSize?: boolean;
}

const WIDTH = 560;
const MAX_HEIGHT = 440;
const PIN_R = 13;

const ZONE_FILL: Record<string, string> = {
  waiting: 'var(--zone-waiting)',
  queue_accumulator: 'var(--zone-queue)',
  back_office: 'rgba(43, 51, 54, 0.07)',
  staff_room: 'rgba(43, 51, 54, 0.07)',
};

export function PlanThumb({ plan, report, showSize }: PlanThumbProps) {
  const PAD = showSize ? 40 : 22;
  const bounds = planBounds(plan);
  const spanX = Math.max(bounds.maxX - bounds.minX, 1);
  const spanY = Math.max(bounds.maxY - bounds.minY, 1);
  const k = Math.min((WIDTH - PAD * 2) / spanX, (MAX_HEIGHT - PAD * 2) / spanY);
  const height = Math.round(spanY * k + PAD * 2);
  const offsetX = (WIDTH - spanX * k) / 2 - PAD;
  const outline = outlinePoints(plan);
  const at = ([x, y]: Point): Point => [(x - bounds.minX) * k + PAD + offsetX, (bounds.maxY - y) * k + PAD];
  const path = (points: Point[]) => `M${points.map((p) => at(p).map((v) => v.toFixed(1)).join(' ')).join('L')}Z`;

  const pins: { n: number; x: number; y: number }[] = [];
  (report?.findings ?? [])
    .filter((f) => f.severity === 'error')
    .forEach((finding, index) => {
      const points = finding.objects
        .map((id) => plan.equipment?.find((e) => e.id === id)?.position)
        .filter((p): p is Point => Boolean(p));
      if (points.length === 0) return;
      let [x, y] = at([
        points.reduce((s, p) => s + p[0], 0) / points.length,
        points.reduce((s, p) => s + p[1], 0) / points.length,
      ]);
      while (pins.some((pin) => Math.hypot(pin.x - x, pin.y - y) < PIN_R * 2.1)) x += PIN_R * 2.1;
      pins.push({ n: index + 1, x, y });
    });

  const conflicted = new Set((report?.findings ?? []).filter((f) => f.severity === 'error').flatMap((f) => f.objects));

  return (
    <svg className="plan-thumb" viewBox={`0 0 ${WIDTH} ${height}`} role="img" aria-label="Пример плана отделения с отмеченными нарушениями">
      {(plan.zones ?? []).map((zone) => (
        <path key={zone.id} d={path(zone.polygon)} fill={ZONE_FILL[zone.type] ?? 'transparent'} />
      ))}
      {(plan.equipment ?? []).map((item) => (
        <path
          key={item.id}
          d={path(equipmentCorners(item))}
          className={`plan-thumb__item${conflicted.has(item.id) ? ' plan-thumb__item--conflict' : ''}`}
        />
      ))}
      {outline && <path d={`${path(outline)}${path(offsetOutline(outline, 6 / k))}`} fillRule="evenodd" className="plan-thumb__ring" />}
      {(plan.walls ?? []).map((wall) => {
        const outer = isOuter(wall);
        if (outer && outline) return null;
        const width = outer ? 6 : 3;
        // Наружная стена лежит снаружи своей линии, как на рабочем плане.
        const [nx, ny] = outer ? outwardNormal(plan, wall, outline) : [0, 0];
        const shift = outer ? width / 2 / k : 0;
        const [ax, ay] = at([wall.from[0] + nx * shift, wall.from[1] + ny * shift]);
        const [bx, by] = at([wall.to[0] + nx * shift, wall.to[1] + ny * shift]);
        return <line key={wall.id} x1={ax} y1={ay} x2={bx} y2={by} strokeWidth={width} className="plan-thumb__wall" />;
      })}
      {(plan.doors ?? []).map((door) => {
        const host = hostWall(plan, door.wall);
        const [nx, ny] = host && isOuter(host) ? outwardNormal(plan, host, outline) : [0, 0];
        const shift = 3 / k;
        const [ax, ay] = at([door.wall[0] + nx * shift, door.wall[1] + ny * shift]);
        const [bx, by] = at([door.wall[2] + nx * shift, door.wall[3] + ny * shift]);
        return <line key={door.id} x1={ax} y1={ay} x2={bx} y2={by} strokeWidth={9} className="plan-thumb__door" />;
      })}
      {showSize && (
        <g className="plan-thumb__size">
          <text x={at([(bounds.minX + bounds.maxX) / 2, bounds.minY])[0]} y={at([0, bounds.minY])[1] + 26} textAnchor="middle">
            {formatNumber(spanX)} м
          </text>
          <text
            textAnchor="middle"
            transform={`translate(${at([bounds.minX, 0])[0] - 22} ${at([0, (bounds.minY + bounds.maxY) / 2])[1]}) rotate(-90)`}
          >
            {formatNumber(spanY)} м
          </text>
        </g>
      )}
      {pins.map((pin, order) => (
        <g key={pin.n} transform={`translate(${pin.x} ${pin.y})`}>
          <g className="plan-thumb__pin" style={{ animationDelay: `${240 + order * 70}ms` }}>
            <circle r={PIN_R} />
            <text textAnchor="middle" dominantBaseline="central">
              {pin.n}
            </text>
          </g>
        </g>
      ))}
    </svg>
  );
}
