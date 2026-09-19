/**
 * Типы цифрового плана отделения — зеркало схемы сервера.
 *
 * Координаты в метрах, начало отсчёта в левом нижнем углу, ось X вправо, ось Y вверх.
 * У оборудования position — центр объекта, sizeM — габариты по осям до поворота,
 * rotationDeg — поворот вокруг центра по часовой стрелке.
 */

export type Point = [number, number];

/** Отрезок стены в виде [x1, y1, x2, y2] — так проёмы задаёт формат заказчика. */
export type WallSegment = [number, number, number, number];

export interface BBox {
  width_m: number;
  height_m: number;
}

export interface OpsMeta {
  id: string;
  type?: string;
  type_label?: string;
  name?: string;
  total_area_sqm?: number;
  bbox?: BBox;
  scale?: string;
  ceiling_height_m?: number;
  /** Население населённого пункта, тыс. человек — для проверки охвата по Приказу № 538. */
  settlement_population?: number;
}

export type WallType = 'outer' | 'partition' | 'internal';

export interface Wall {
  id: string;
  from: Point;
  to: Point;
  type?: WallType | string;
  label?: string;
}

export type DoorType = 'entrance' | 'service' | 'internal';

export interface Door {
  id: string;
  wall: WallSegment;
  type?: DoorType | string;
  width_m: number;
  label?: string;
}

export interface WindowOpening {
  id: string;
  wall: WallSegment;
  height_m?: number;
}

export interface Zone {
  id: string;
  type: string;
  label?: string;
  polygon: Point[];
  area_sqm?: number;
  expected_capacity_persons?: number;
}

export interface Inventory {
  serial_number?: string;
  vendor?: string;
  model?: string;
  last_inspection?: string;
  next_inspection?: string;
}

export interface Staff {
  full_name?: string;
  position?: string;
  personnel_number?: string;
  schedule?: string;
  avg_service_time_min?: number;
}

export interface Equipment {
  id: string;
  type: string;
  label?: string;
  position: Point;
  size_m: [number, number];
  rotation_deg?: number;
  zone?: string;
  /** Рабочее место оператора со служебной стороны окна обслуживания. */
  operator_position?: Point;
  capacity_persons?: number;
  height_m?: number;
  /** Высота низа окна или стойки обслуживания. */
  counter_height_m?: number;
  attributes?: Record<string, unknown>;
  inventory?: Inventory;
  staff?: Staff;
  integrations?: Record<string, string>;
}

export interface TypicalPath {
  label?: string;
  waypoints: Point[];
  frequency_share?: number;
}

export interface FlowHints {
  main_entrance?: string;
  typical_paths?: TypicalPath[];
  typical_visit_duration_min?: number;
  peak_hours_local?: string[];
  avg_daily_visitors?: number;
}

export interface ZoneTypeDef {
  code: string;
  label: string;
}

export interface EquipmentTypeDef {
  code: string;
  label: string;
  group?: string;
  default_size_m?: [number, number];
  floor_object?: boolean;
  allowed_zones?: string[];
  capacity_persons?: number | null;
  operator_workplace?: boolean;
  height_m?: number;
  mount_height_m?: number;
  attribute_schema?: Record<string, string>;
}

/** Справочники хранятся внутри плана, поэтому расширяются без изменения кода. */
export interface Dictionaries {
  zone_types?: ZoneTypeDef[];
  equipment_types?: EquipmentTypeDef[];
}

export interface Plan {
  ops: OpsMeta;
  walls?: Wall[];
  doors?: Door[];
  windows?: WindowOpening[];
  zones?: Zone[];
  equipment?: Equipment[];
  flow_hints?: FlowHints;
  dictionaries?: Dictionaries;
}

export type Severity = 'error' | 'warning' | 'info';
export type RuleCategory = 'geometry' | 'accessibility' | 'technology' | 'capacity';

export interface Rule {
  id: string;
  title: string;
  category: RuleCategory;
  severity: Severity;
  source: string;
  clause: string;
  value?: number | null;
  unit: string;
  rationale: string;
  enabled: boolean;
}

export interface Finding {
  rule_id: string;
  title: string;
  severity: Severity;
  category: RuleCategory;
  source: string;
  clause: string;
  message: string;
  objects: string[];
  measured?: number | null;
  required?: number | null;
  unit: string;
}

export interface ValidationReport {
  findings: Finding[];
  errors: number;
  warnings: number;
  infos: number;
  checked_rules: string[];
  skipped_rules: string[];
  skipped_reasons?: Record<string, string>;
}

export interface PlanMetrics {
  'площадь_общая_м2': number | null;
  'площадь_занятая_м2': number;
  'площадь_свободная_м2': number | null;
  'площади_зон_м2': Record<string, number>;
  'оборудование_всего': number;
  'оборудование_по_типам': Record<string, number>;
  'оборудование_по_зонам': Record<string, number>;
}

/** Пустой план для создания отделения с нуля. */
export function emptyPlan(id: string, name: string): Plan {
  return {
    ops: { id, name, scale: '1 unit = 1 meter' },
    walls: [],
    doors: [],
    windows: [],
    zones: [],
    equipment: [],
  };
}

export function equipmentCorners(item: Equipment): Point[] {
  const [cx, cy] = item.position;
  const [width, height] = item.size_m;
  const halfW = width / 2;
  const halfH = height / 2;
  const angle = (-(item.rotation_deg ?? 0) * Math.PI) / 180;
  const cos = Math.cos(angle);
  const sin = Math.sin(angle);
  return (
    [
      [-halfW, -halfH],
      [halfW, -halfH],
      [halfW, halfH],
      [-halfW, halfH],
    ] as Point[]
  ).map(([x, y]) => [cx + x * cos - y * sin, cy + x * sin + y * cos] as Point);
}

export function planBounds(plan: Plan): { minX: number; minY: number; maxX: number; maxY: number } {
  const xs: number[] = [];
  const ys: number[] = [];
  const push = (p: Point) => {
    xs.push(p[0]);
    ys.push(p[1]);
  };
  plan.walls?.forEach((w) => {
    push(w.from);
    push(w.to);
  });
  plan.zones?.forEach((z) => z.polygon.forEach(push));
  plan.equipment?.forEach((e) => equipmentCorners(e).forEach(push));
  if (xs.length === 0) {
    const width = plan.ops.bbox?.width_m ?? 10;
    const height = plan.ops.bbox?.height_m ?? 8;
    return { minX: 0, minY: 0, maxX: width, maxY: height };
  }
  return {
    minX: Math.min(...xs),
    minY: Math.min(...ys),
    maxX: Math.max(...xs),
    maxY: Math.max(...ys),
  };
}

export interface ZoneTypeInfo {
  code: string;
  label: string;
  public: boolean;
}

export interface EquipmentTypeInfo {
  code: string;
  label: string;
  group: string;
  default_size_m: [number, number];
  floor_object: boolean;
  allowed_zones: string[];
  capacity_persons?: number | null;
  /** У объекта есть место сотрудника: касса, стойка выдачи, рабочее место. */
  operator_workplace?: boolean;
  /** Высота объекта и высота его низа над полом — для объёмного вида. */
  height_m?: number;
  mount_height_m?: number;
  attribute_schema: Record<string, string>;
}

export interface DictionariesInfo {
  zone_types: ZoneTypeInfo[];
  equipment_types: EquipmentTypeInfo[];
}

export interface Reason {
  text: string;
  source?: string | null;
  clause?: string | null;
}

export interface Effect {
  key: string;
  label: string;
  before: number | null;
  after: number | null;
  unit: string;
  improved: boolean | null;
}

export interface RecommendationChange {
  op: 'add' | 'move' | 'remove';
  object_id: string;
  equipment_type: string;
  label?: string | null;
  position?: [number, number] | null;
  rotation_deg?: number | null;
  size_m?: [number, number] | null;
  zone?: string | null;
  operator_position?: [number, number] | null;
  previous_position?: [number, number] | null;
  previous_rotation_deg?: number | null;
}

export interface Recommendation {
  id: string;
  scenario: 'fix' | 'missing' | 'excess' | 'capacity' | 'placement' | string;
  priority: number;
  title: string;
  summary: string;
  changes: RecommendationChange[];
  reasons: Reason[];
  assumptions: string[];
  effects: Effect[];
  objects: string[];
  zones: string[];
  alternatives: RecommendationChange[];
}

export interface QueueGroup {
  name: string;
  windows: string[];
  arrivals_per_hour: number;
  service_min: number;
  utilization: number;
  stable: boolean;
  wait_probability?: number | null;
  mean_wait_min?: number | null;
  mean_queue?: number | null;
  queue_p95?: number | null;
  windows_needed: number;
  service_threshold_min?: number | null;
  reserve_percent?: number | null;
}

export interface CapacityReport {
  available: boolean;
  reason?: string | null;
  daily_visitors?: number | null;
  peak_arrivals_per_hour?: number | null;
  groups: QueueGroup[];
  waiting_capacity_persons?: number | null;
  peak_queue_persons?: number | null;
  fits_waiting_area?: boolean | null;
  assumptions: string[];
}

export interface AssistantReport {
  recommendations: Recommendation[];
  capacity: CapacityReport;
  summary: Record<string, unknown>;
  elapsed_ms: number;
}

export interface ImportResult {
  document: Plan;
  notes: string[];
  stats: Record<string, string | number>;
  labels_source: string;
}

export interface CommandResult {
  understood: string[];
  unknown: string[];
  source: string;
  recommendations: Recommendation[];
  elapsed_ms: number;
}

export interface Conclusion {
  text: string;
  source: string;
  facts: Record<string, unknown>;
}

export interface LanguageState {
  'модель_подключена': boolean;
  'почему_нет': string;
  'модель': string | null;
}

export interface ImprovementReport {
  initial: Record<string, unknown>;
  final: Record<string, unknown>;
  steps: { recommendation: Recommendation; errors_after: number; warnings_after: number }[];
  improved_plan: Plan;
  effects: Effect[];
  elapsed_ms: number;
}
