/**
 * Применение рекомендаций в редакторе обязано делать ровно то же, что сервер: добавлять,
 * переставлять и убирать объекты. Раньше «убрать» не убирало — объект оставался на плане.
 */

import { beforeEach, describe, expect, it } from 'vitest';
import type { Finding, Plan, RecommendationChange, ValidationReport } from '../lib/plan';
import { useWorkspace } from './store';

const plan = (): Plan => ({
  ops: { id: 'ops_test', name: 'Тестовое отделение', bbox: { width_m: 10, height_m: 8 } },
  walls: [
    { id: 'w1', from: [0, 0], to: [10, 0], type: 'outer' },
    { id: 'w2', from: [10, 0], to: [10, 8], type: 'outer' },
    { id: 'w3', from: [10, 8], to: [0, 8], type: 'outer' },
    { id: 'w4', from: [0, 8], to: [0, 0], type: 'outer' },
  ],
  zones: [
    { id: 'z_wait', type: 'waiting', label: 'Зона ожидания', polygon: [[0, 0], [4, 0], [4, 4], [0, 4]] },
  ],
  equipment: [
    { id: 'eq_bench', type: 'waiting_seat', label: 'Скамья', position: [2, 2], size_m: [2, 0.5], zone: 'z_wait' },
    { id: 'eq_atm', type: 'atm', label: 'Банкомат', position: [8, 6], size_m: [0.7, 0.7] },
  ],
}) as Plan;

const change = (
  partial: Partial<RecommendationChange> & { op: RecommendationChange['op']; object_id: string },
): RecommendationChange =>
  ({ equipment_type: 'atm', ...partial }) as RecommendationChange;

describe('применение рекомендаций', () => {
  beforeEach(() => {
    useWorkspace.getState().open('p1', 'Тест', 1, plan());
  });

  it('убирает объект с плана', () => {
    useWorkspace.getState().applyChanges([change({ op: 'remove', object_id: 'eq_atm' })]);
    const ids = useWorkspace.getState().doc!.equipment!.map((item) => item.id);
    expect(ids).toEqual(['eq_bench']);
  });

  it('переставляет объект и снимает привязку к покинутой зоне', () => {
    useWorkspace
      .getState()
      .applyChanges([change({ op: 'move', object_id: 'eq_bench', equipment_type: 'waiting_seat', position: [8, 7] })]);
    const bench = useWorkspace.getState().doc!.equipment!.find((item) => item.id === 'eq_bench')!;
    expect(bench.position).toEqual([8, 7]);
    expect(bench.zone).toBeUndefined();
  });

  it('привязывает объект к зоне, в которую его переставили', () => {
    useWorkspace
      .getState()
      .applyChanges([change({ op: 'move', object_id: 'eq_atm', position: [1, 1] })]);
    const atm = useWorkspace.getState().doc!.equipment!.find((item) => item.id === 'eq_atm')!;
    expect(atm.zone).toBe('z_wait');
  });

  it('добавляет новый объект', () => {
    useWorkspace.getState().applyChanges([
      change({ op: 'add', object_id: 'eq_new', equipment_type: 'mailbox', position: [6, 2], size_m: [0.5, 0.4] }),
    ]);
    const added = useWorkspace.getState().doc!.equipment!.find((item) => item.id === 'eq_new');
    expect(added?.position).toEqual([6, 2]);
  });
});

const finding = (rule_id: string, objects: string[], severity: Finding['severity'] = 'error'): Finding => ({
  rule_id,
  title: rule_id,
  severity,
  category: 'accessibility',
  source: 'СП 59.13330.2020',
  clause: 'п. 6.2.1',
  message: '',
  objects,
  unit: 'м',
});

const report = (...findings: Finding[]): ValidationReport => ({
  findings,
  errors: findings.filter((f) => f.severity === 'error').length,
  warnings: findings.filter((f) => f.severity === 'warning').length,
  infos: 0,
  checked_rules: [],
  skipped_rules: [],
});

describe('закреплённое нарушение', () => {
  const narrow = finding('movement_path_width', ['eq_atm', 'eq_bench']);
  const overlap = finding('no_overlaps', ['eq_bench', 'eq_atm']);
  const height = finding('counter_height', ['eq_atm'], 'warning');

  beforeEach(() => {
    const state = useWorkspace.getState();
    state.open('p1', 'Тест', 1, plan());
    state.setReport(report(narrow, overlap, height), 'ready');
    state.setActiveFinding(1);
    state.highlight(overlap.objects);
  });

  it('остаётся закреплённым, когда после перепроверки у него другой номер', () => {
    // Узкий проход исправили: пересечение стало первым в списке.
    useWorkspace.getState().setReport(report(finding('no_overlaps', ['eq_atm', 'eq_bench']), height), 'ready');
    const state = useWorkspace.getState();
    expect(state.activeFinding).toBe(0);
    expect(state.highlighted).toEqual(overlap.objects);
  });

  it('снимается вместе с подсветкой, когда его исправили', () => {
    useWorkspace.getState().setReport(report(narrow, height), 'ready');
    const state = useWorkspace.getState();
    expect(state.activeFinding).toBeNull();
    expect(state.highlighted).toEqual([]);
  });

  it('не трогает чужую подсветку, когда исправлено', () => {
    useWorkspace.getState().highlight(['eq_bench']);
    useWorkspace.getState().setReport(report(narrow, height), 'ready');
    expect(useWorkspace.getState().highlighted).toEqual(['eq_bench']);
  });

  it('не сбивается, пока идёт перепроверка того же отчёта', () => {
    const state = useWorkspace.getState();
    state.setReport(state.report, 'checking');
    expect(useWorkspace.getState().activeFinding).toBe(1);
  });
});
