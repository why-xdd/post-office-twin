/**
 * Изменение положения и поворота объекта вместе с местом оператора.
 *
 * Место оператора хранится в координатах плана, а не относительно объекта. Поэтому при сдвиге
 * оно сдвигается на тот же вектор, а при повороте поворачивается вокруг центра объекта —
 * иначе стул остаётся там, где стояло окно.
 */

import type { Equipment, Plan, Point } from './plan';
import { pointInPolygon, round3 } from './geometry';
import { outlinePoints } from './planFacts';

export type EquipmentPatch = Partial<Equipment>;

export function withTransform(item: Equipment, patch: EquipmentPatch): EquipmentPatch {
  if (!item.operator_position || 'operator_position' in patch) return patch;
  let [ox, oy] = item.operator_position;
  const from = item.position;
  if (patch.rotation_deg !== undefined && patch.rotation_deg !== (item.rotation_deg ?? 0)) {
    // Поворот в плане — по часовой стрелке при росте угла, как у самого габарита.
    const delta = (-(patch.rotation_deg - (item.rotation_deg ?? 0)) * Math.PI) / 180;
    const dx = ox - from[0];
    const dy = oy - from[1];
    ox = from[0] + dx * Math.cos(delta) - dy * Math.sin(delta);
    oy = from[1] + dx * Math.sin(delta) + dy * Math.cos(delta);
  }
  if (patch.position) {
    ox += patch.position[0] - from[0];
    oy += patch.position[1] - from[1];
  }
  const operator: Point = [round3(ox), round3(oy)];
  return { ...patch, operator_position: operator };
}

/** Применяет изменение к объекту на месте: для черновиков в commit и transient. */
export function transformInPlace(target: Equipment, patch: EquipmentPatch): void {
  Object.assign(target, withTransform(target, patch));
}

const OPERATOR_GAP_M = 0.4;

/**
 * Место сотрудника у рабочего места: с той стороны объекта, где есть место внутри помещения.
 * Оператор стоит за стойкой, а не в стене и не на улице.
 */
export function defaultOperatorPosition(plan: Plan, item: Equipment): Point {
  const outline = outlinePoints(plan);
  const angle = (-(item.rotation_deg ?? 0) * Math.PI) / 180;
  const reach = item.size_m[1] / 2 + OPERATOR_GAP_M;
  const options: Point[] = [
    [item.position[0] - Math.sin(angle) * -reach, item.position[1] + Math.cos(angle) * -reach],
    [item.position[0] - Math.sin(angle) * reach, item.position[1] + Math.cos(angle) * reach],
  ].map(([x, y]) => [round3(x), round3(y)]);
  const fits = options.find((point) => !outline || pointInPolygon(point, outline));
  return fits ?? options[0];
}
