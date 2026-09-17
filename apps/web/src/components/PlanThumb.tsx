/**
 * Миниатюра плана с метками нарушений — для первого экрана. Только рисунок: без масштаба,
 * выделения и подписей, чтобы с первого взгляда было видно, что именно делает сервис.
 */

import type { Plan, Point, ValidationReport } from '../lib/plan';
import { equipmentCorners, planBounds } from '../lib/plan';
import './plan-thumb.css';

interface PlanThumbProps {
  plan: Plan;
  report: ValidationReport | null;
}

const WIDTH = 560;
const PAD = 22;
const PIN_R = 13;

const ZONE_FILL: Record<string, string> = {
  waiting: 'var(--zone-waiting)',
  queue_accumulator: 'var(--zone-queue)',
  back_office: 'rgba(43, 51, 54, 0.07)',
  staff_room: 'rgba(43, 51, 54, 0.07)',
};

export function PlanThumb({ plan, report }: PlanThumbProps) {
  const bounds = planBounds(plan);
  const k = (WIDTH - PAD * 2) / Math.max(bounds.maxX - bounds.minX, 1);
  const height = Math.round((bounds.maxY - bounds.minY) * k + PAD * 2);
  const at = ([x, y]: Point): Point => [(x - bounds.minX) * k + PAD, (bounds.maxY - y) * k + PAD];
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
      {(plan.walls ?? []).map((wall) => {
        const [ax, ay] = at(wall.from);
        const [bx, by] = at(wall.to);
        const outer = (wall.type ?? 'outer') === 'outer';
        return <line key={wall.id} x1={ax} y1={ay} x2={bx} y2={by} strokeWidth={outer ? 6 : 3} className="plan-thumb__wall" />;
      })}
      {(plan.doors ?? []).map((door) => {
        const [ax, ay] = at([door.wall[0], door.wall[1]]);
        const [bx, by] = at([door.wall[2], door.wall[3]]);
        return <line key={door.id} x1={ax} y1={ay} x2={bx} y2={by} strokeWidth={8} className="plan-thumb__door" />;
      })}
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
