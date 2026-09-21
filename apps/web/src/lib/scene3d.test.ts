import { describe, expect, it } from 'vitest';
import type { EquipmentTypeInfo, Plan, ValidationReport } from './plan';
import { DOOR_HEIGHT_M, buildScene } from './scene3d';

function types(): Record<string, EquipmentTypeInfo> {
  const base = { group: 'Тест', allowed_zones: [], attribute_schema: {} };
  return {
    cashier_window: { ...base, code: 'cashier_window', label: 'Касса', default_size_m: [1.2, 0.8], floor_object: true, height_m: 1.15, mount_height_m: 0 },
    info_display: { ...base, code: 'info_display', label: 'Табло', default_size_m: [1.1, 0.1], floor_object: false, height_m: 0.65, mount_height_m: 2.1 },
  };
}

function plan(): Plan {
  return {
    ops: { id: 't', name: 'Тест', ceiling_height_m: 3 },
    walls: [
      { id: 's', from: [0, 0], to: [10, 0], type: 'outer' },
      { id: 'e', from: [10, 0], to: [10, 6], type: 'outer' },
      { id: 'n', from: [10, 6], to: [0, 6], type: 'outer' },
      { id: 'w', from: [0, 6], to: [0, 0], type: 'outer' },
    ],
    doors: [{ id: 'd', wall: [2, 6, 3.5, 6], type: 'entrance', width_m: 1.5 }],
    equipment: [
      { id: 'eq_1', type: 'cashier_window', label: 'Касса 1', position: [5, 2], size_m: [1.2, 0.8], rotation_deg: 0 },
      { id: 'eq_2', type: 'info_display', label: 'Табло', position: [8, 5.9], size_m: [1.1, 0.1], rotation_deg: 0 },
    ],
    flow_hints: { main_entrance: 'd', typical_paths: [{ label: 'К кассе', waypoints: [[2.75, 6], [5, 2.8]], frequency_share: 0.6 }] },
  } as Plan;
}

describe('объёмная сцена', () => {
  it('разрезает стену проёмом и оставляет перемычку над дверью', () => {
    const scene = buildScene(plan(), types());
    const parts = scene.prisms.filter((p) => p.id.startsWith('n:'));
    // Северная стена: кусок слева от двери, кусок справа и перемычка над ней.
    expect(parts).toHaveLength(3);
    const lintel = parts.find((p) => p.base === DOOR_HEIGHT_M);
    expect(lintel).toBeDefined();
    expect(lintel?.height).toBeCloseTo(3 - DOOR_HEIGHT_M, 3);
    expect(lintel?.size[0]).toBeCloseTo(1.5, 2);
    // Ни один кусок стены не перекрывает сам проём на уровне человека.
    const atEyeLevel = parts.filter((p) => p.base < 1.6 && p.base + p.height > 1.6);
    expect(atEyeLevel).toHaveLength(2);
  });

  it('берёт высоту из справочника, включая высоту подвеса', () => {
    const scene = buildScene(plan(), types());
    const cashier = scene.prisms.find((p) => p.id === 'eq_1');
    const display = scene.prisms.find((p) => p.id === 'eq_2');
    expect(cashier?.height).toBeCloseTo(1.15, 3);
    expect(cashier?.base).toBe(0);
    expect(display?.base).toBeCloseTo(2.1, 3);
    expect(display?.height).toBeCloseTo(0.65, 3);
  });

  it('своя высота объекта важнее справочной', () => {
    const document = plan();
    (document.equipment ?? [])[0].height_m = 2.4;
    const scene = buildScene(document, types());
    expect(scene.prisms.find((p) => p.id === 'eq_1')?.height).toBeCloseTo(2.4, 3);
  });

  it('помечает виновников нарушений и считает их', () => {
    const report = {
      findings: [
        { rule_id: 'r', title: 'Узкий проход', severity: 'error', category: 'norm', source: '', clause: '', message: '', objects: ['eq_1'], unit: 'м' },
      ],
      errors: 1,
      warnings: 0,
      infos: 0,
      checked_rules: [],
      skipped_rules: [],
    } as unknown as ValidationReport;
    const scene = buildScene(plan(), types(), report);
    expect(scene.prisms.find((p) => p.id === 'eq_1')?.violation).toBe(true);
    expect(scene.prisms.find((p) => p.id === 'eq_2')?.violation).toBe(false);
    expect(scene.violations).toBe(1);
  });

  it('предупреждение красит объект янтарным, а не красным', () => {
    const report = {
      findings: [
        { rule_id: 'r', title: 'Не в своей зоне', severity: 'warning', category: 'integrity', source: '', clause: '', message: '', objects: ['eq_2'], unit: '' },
      ],
      errors: 0,
      warnings: 1,
      infos: 0,
      checked_rules: [],
      skipped_rules: [],
    } as unknown as ValidationReport;
    const scene = buildScene(plan(), types(), report);
    const display = scene.prisms.find((p) => p.id === 'eq_2');
    expect(display?.violation).toBe(false);
    expect(display?.caution).toBe(true);
    expect(scene.violations).toBe(0);
    expect(scene.cautions).toBe(1);
  });

  it('переносит типовые маршруты и ставит фигуры у входа внутрь помещения', () => {
    const scene = buildScene(plan(), types());
    expect(scene.ribbons).toHaveLength(1);
    expect(scene.ribbons[0].share).toBeCloseTo(0.6, 3);
    expect(scene.figures).toHaveLength(2);
    for (const figure of scene.figures) {
      expect(figure.centre[1]).toBeLessThan(6);
      expect(figure.centre[1]).toBeGreaterThan(0);
    }
  });

  it('работает без отчёта, зон и маршрутов', () => {
    const bare = { ops: { id: 'b', name: 'Пусто' }, walls: [], equipment: [] } as unknown as Plan;
    const scene = buildScene(bare, types());
    expect(scene.prisms).toHaveLength(0);
    expect(scene.ribbons).toHaveLength(0);
    expect(scene.radius).toBeGreaterThan(0);
  });
});
