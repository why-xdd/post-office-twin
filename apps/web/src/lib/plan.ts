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
  /** Целевое среднее ожидание в час пик, мин; по умолчанию 10 — цель Почты России. */
  target_wait_min?: number;
  /** Посылок в день — для ёмкости кладовой по ВНТП 311-98 п. 3.10. */
  parcels_in_per_day?: number;
  parcels_out_per_day?: number;
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
  inventory_number?: string;
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
export type RuleCategory = 'geometry' | 'accessibility' | 'technology' | 'capacity' | 'fire';

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
  /** Где на плане замер: отрезок самого узкого просвета, если сервер его нашёл. */
  gap?: [Point, Point] | null;
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

/** Сценарий пика: что меняет начальник отделения или проектировщик. */
export interface PeakScenario {
  flow_factor: number;
  self_service_share: number;
  closed: string[];
  out_of_service: string[];
}

export interface QueuePerson {
  x: number;
  y: number;
  group: string;
  /** Стоит не там, где положено: за накопителем, в тамбуре, на проходе. */
  outside: boolean;
}

export interface PeakGroup {
  name: string;
  windows: string[];
  working: string[];
  arrivals_per_hour: number;
  service_min: number;
  /** null — очередь растёт без предела. */
  mean_wait_min: number | null;
  meets_target: boolean;
  people: number;
  windows_needed: number;
  queue_length_m: number;
}

export interface PeakReport {
  available: boolean;
  reason?: string | null;
  scenario: PeakScenario;
  target_wait_min: number;
  peak_arrivals_per_hour?: number | null;
  groups: PeakGroup[];
  people: QueuePerson[];
  accumulator_slots?: number | null;
  outside: number;
  spills_into: string[];
  beyond_entrance: number;
  passage_without_queue_m?: number | null;
  passage_with_queue_m?: number | null;
  evacuation_required_m?: number | null;
  evacuation_with_queue_m?: number | null;
  evacuation_zone?: string | null;
  advice: string[];
  assumptions: string[];
}

export interface StorageItem {
  id: string;
  label: string;
  places: number;
  estimated: boolean;
}

export interface StorageReport {
  available: boolean;
  reason?: string | null;
  factor: number;
  items: StorageItem[];
  places: number;
  incoming_per_day?: number | null;
  outgoing_per_day?: number | null;
  required?: number | null;
  enough?: boolean | null;
  deficit: number;
  source: string;
  advice: string[];
  assumptions: string[];
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
  /** Отведённое время кончилось: показано то, что сервис успел подобрать. */
  partial?: boolean;
}

export interface ImportResult {
  document: Plan;
  notes: string[];
  stats: Record<string, string | number>;
}

export interface CommandResult {
  understood: string[];
  unknown: string[];
  source: string;
  /** Ответы на вопросы о плане: «сколько окон», «где банкомат», «какая свободная площадь». */
  answers?: string[];
  recommendations: Recommendation[];
  elapsed_ms: number;
}

export interface Conclusion {
  text: string;
  facts: Record<string, unknown>;
}

export interface ImprovementReport {
  initial: Record<string, unknown>;
  final: Record<string, unknown>;
  steps: { recommendation: Recommendation; errors_after: number; warnings_after: number }[];
  improved_plan: Plan;
  effects: Effect[];
  elapsed_ms: number;
  /** Отведённое время кончилось: показаны найденные шаги. */
  partial?: boolean;
  /** Почему осталось то, что осталось: например, окна кассы движок не переставляет. */
  notes?: string[];
}

/** Сверка плана с ведомостью основных средств из учётной системы (1С). */
export interface AccountingRow {
  line: number;
  inventory_number?: string | null;
  name?: string | null;
  serial_number?: string | null;
  location?: string | null;
  plan_id?: string | null;
}

export interface ReconcileMatch {
  object_id: string;
  label: string;
  inventory_number?: string | null;
  zone?: string | null;
  accounting_location?: string | null;
  location_differs: boolean;
  serial_differs: boolean;
  matched_by: string;
}

export interface ReconcileSurplus {
  object_id: string;
  label: string;
  inventory_number?: string | null;
  serial_number?: string | null;
  zone?: string | null;
}

export interface Reconciliation {
  matched: ReconcileMatch[];
  missing_on_plan: AccountingRow[];
  not_in_accounting: ReconcileSurplus[];
  location_mismatches: number;
  serial_mismatches: number;
  rows_read: number;
  columns_recognised: Record<string, string>;
  summary: string;
}

/** Очередь по фактическим данным: журнал электронной очереди или счётчик посетителей. */
export interface QueueHour {
  hour: number;
  arrivals_per_hour: number;
  measured_wait_min?: number | null;
  measured_wait_p90_min?: number | null;
  measured_service_min?: number | null;
  windows_open?: number | null;
  model_wait_min?: number | null;
  stable: boolean;
  windows_needed: number;
  short: boolean;
}

export interface QueueGroupHours {
  name: string;
  windows_on_plan: number;
  service_min: number;
  service_source: string;
  hours: QueueHour[];
  share_waited_over_limit?: number | null;
  staff_hours_needed: number;
  staff_hours_open?: number | null;
}

export interface QueueLogReport {
  source: string;
  days: number;
  records: number;
  skipped_rows: number;
  groups: QueueGroupHours[];
  peak_hours: string[];
  declared_peak_hours: string[];
  summary: string[];
  assumptions: string[];
  weekdays?: QueueWeekday[];
}

export interface QueueWeekday {
  weekday: number;
  label: string;
  days: number;
  group: string;
  hours: { hour: number; arrivals_per_hour: number; windows_needed: number }[];
  window_hours: number;
}

/** Разбор записи с камеры зала: сколько людей, где стоят, хватает ли мест. */
export interface VisionZoneLoad {
  zone_id?: string | null;
  label: string;
  mean_people: number;
  peak_people: number;
  seats?: number | null;
  over_capacity_share?: number | null;
}

export interface VisionMoment {
  second: number;
  people: number;
}

export interface VisionHeatmap {
  origin: [number, number];
  cell_m: number;
  cols: number;
  rows: number;
  values: number[];
}

export interface VisionReport {
  frames: number;
  duration_s: number;
  people_mean: number;
  people_peak: number;
  zones: VisionZoneLoad[];
  timeline: VisionMoment[];
  heatmap: VisionHeatmap;
  summary: string[];
  assumptions: string[];
}

export interface User {
  login: string;
  name: string;
  role: 'head' | 'operator' | 'engineer' | 'designer' | string;
  role_label: string;
}

export interface AuthState {
  user: User | null;
  demo: boolean;
  roles: { role: string; label: string }[];
}

export type IncidentStatus = 'new' | 'in_progress' | 'resolved';

export interface Incident {
  id: string;
  plan_id: string;
  object_id: string;
  object_label: string;
  text: string;
  out_of_service: boolean;
  status: IncidentStatus;
  status_label: string;
  created_at: string;
  created_by: string;
  history: { at: string; status: IncidentStatus; by: string; note?: string | null }[];
}

export interface TodayAction {
  text: string;
  kind: 'incident' | 'inspection' | 'windows' | 'check' | 'storage';
  object_id?: string | null;
}

export interface TodayReport {
  date: string;
  weekday: string;
  plan_title: string;
  user: User | null;
  incidents: Incident[];
  out_of_service: string[];
  inspections: { object_id: string; label: string; due: string; days: number }[];
  windows: {
    name: string;
    total: number;
    working: number;
    needed: number;
    mean_wait_min: number | null;
    meets_target: boolean;
  }[];
  windows_note?: string | null;
  target_wait_min?: number | null;
  errors: number;
  warnings: number;
  storage_short?: number | null;
  actions: TodayAction[];
}

export interface NetworkFactor {
  key: 'fire' | 'accessibility' | 'windows' | 'other' | 'out_of_service' | 'overdue' | string;
  count: number;
  points: number;
  text: string;
}

export interface NetworkOffice {
  plan_id: string;
  title: string;
  area_sqm: number | null;
  daily_visitors: number | null;
  score: number;
  rank: number;
  factors: NetworkFactor[];
  errors: number;
  worst_wait_min: number | null;
  queue_unbounded: boolean;
  failed: string | null;
}

export interface NetworkReport {
  offices: NetworkOffice[];
  weights: { key: string; weight: number; label: string }[];
  total_errors: number;
  offices_with_fire: number;
  offices_with_accessibility: number;
  windows_short: number;
}

export interface Prices {
  move?: number | null;
  remove?: number | null;
  install?: number | null;
  wall_per_m?: number | null;
  wall_removal_per_m?: number | null;
  equipment: Record<string, number>;
}

export interface Comparison {
  added: { id: string; label: string; type: string }[];
  removed: { id: string; label: string; type: string }[];
  moved: { id: string; label: string; type: string }[];
  walls_changed_m: number;
  walls_removed_m?: number;
  metrics: { key: string; label: string; before: number | null; after: number | null; unit: string; better: boolean | null }[];
  lines: { text: string; quantity: number; unit: string; price: number | null; total: number | null; key: string }[];
  total: number;
  complete: boolean;
  missing: string[];
}
