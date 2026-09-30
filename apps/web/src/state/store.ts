/**
 * Состояние рабочего пространства.
 *
 * План хранится как документ формата заказчика. Каждое изменение проходит через commit и
 * попадает в историю, поэтому отмена и повтор работают для любой правки одинаково.
 * Перетаскивание — одна правка: промежуточные положения не засоряют историю.
 */

import { create } from 'zustand';
import { transformInPlace } from '../lib/equipmentOps';
import { zoneAt } from '../lib/geometry';
import { removeWall } from '../lib/wallEdit';
import type { Equipment, Plan, ValidationReport, VisionHeatmap, Zone } from '../lib/plan';

export type SelectionKind = 'equipment' | 'zone' | 'wall' | 'door' | 'window';

export interface Selection {
  kind: SelectionKind;
  id: string;
}

export type Tool = 'select' | 'zone' | 'wall' | 'outline' | 'equipment';
/** Обзор — простой показ нарушений и исправления; редактор и эксплуатация — полные режимы. */
export type Mode = 'review' | 'design' | 'operate';
export type ViewKind = '2d' | '3d';
export type SidePanel = 'review' | 'properties' | 'checks' | 'assistant' | 'inventory' | 'passport' | 'camera';

export interface Change {
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

const HISTORY_LIMIT = 120;

const clone = <T,>(value: T): T => structuredClone(value);

interface WorkspaceState {
  planId: string | null;
  title: string;
  version: number;
  doc: Plan | null;
  savedSnapshot: string;
  past: Plan[];
  future: Plan[];
  transientBase: Plan | null;

  selection: Selection | null;
  highlighted: string[];
  tool: Tool;
  placingType: string | null;
  mode: Mode;
  view: ViewKind;
  panel: SidePanel;

  report: ValidationReport | null;
  reportState: 'idle' | 'checking' | 'ready' | 'error';
  preview: Change[] | null;
  /** Объекты, только что вставшие на место по рекомендации: для движения «защёлкивания». */
  latched: string[];
  /** Номер нарушения, открытого в обзоре: по нему на плане выделяется метка. */
  activeFinding: number | null;
  /** Тепловая карта посещаемости по записи с камеры: рисуется слоем поверх плана. */
  heatmap: VisionHeatmap | null;

  open: (planId: string | null, title: string, version: number, doc: Plan) => void;
  close: () => void;
  commit: (mutate: (draft: Plan) => void) => void;
  beginTransient: () => void;
  transient: (mutate: (draft: Plan) => void) => void;
  endTransient: () => void;
  cancelTransient: () => void;
  undo: () => void;
  redo: () => void;
  markSaved: (planId: string, version: number) => void;
  /** Открыть прежнюю версию как правку текущей: её можно отменить и сохранить как новую. */
  restoreVersion: (doc: Plan) => void;

  select: (selection: Selection | null) => void;
  highlight: (ids: string[]) => void;
  setTool: (tool: Tool, placingType?: string | null) => void;
  setMode: (mode: Mode) => void;
  setView: (view: ViewKind) => void;
  setPanel: (panel: SidePanel) => void;
  setHeatmap: (heatmap: VisionHeatmap | null) => void;
  setReport: (report: ValidationReport | null, state: WorkspaceState['reportState']) => void;
  setPreview: (changes: Change[] | null) => void;
  latch: (ids: string[]) => void;
  setActiveFinding: (index: number | null) => void;

  updateEquipment: (id: string, patch: Partial<Equipment>) => void;
  updateZone: (id: string, patch: Partial<Zone>) => void;
  removeSelection: () => void;
  applyChanges: (changes: Change[]) => void;
}

export const useWorkspace = create<WorkspaceState>((set, get) => ({
  planId: null,
  title: '',
  version: 0,
  doc: null,
  savedSnapshot: '',
  past: [],
  future: [],
  transientBase: null,

  selection: null,
  highlighted: [],
  tool: 'select',
  placingType: null,
  mode: 'review',
  view: '2d',
  panel: 'review',

  report: null,
  reportState: 'idle',
  preview: null,
  latched: [],
  activeFinding: null,
  heatmap: null,

  open: (planId, title, version, doc) =>
    set({
      planId,
      title,
      version,
      doc: clone(doc),
      savedSnapshot: JSON.stringify(doc),
      past: [],
      future: [],
      transientBase: null,
      selection: null,
      highlighted: [],
      tool: 'select',
      placingType: null,
      preview: null,
      report: null,
      reportState: 'idle',
      mode: 'review',
      panel: 'review',
      activeFinding: null,
      heatmap: null,
    }),

  close: () => set({ doc: null, planId: null, title: '', past: [], future: [], preview: null }),

  commit: (mutate) => {
    const { doc, past } = get();
    if (!doc) return;
    const next = clone(doc);
    mutate(next);
    set({ doc: next, past: [...past, doc].slice(-HISTORY_LIMIT), future: [], preview: null });
  },

  beginTransient: () => {
    const { doc } = get();
    if (doc) set({ transientBase: doc });
  },

  transient: (mutate) => {
    const { doc } = get();
    if (!doc) return;
    const next = clone(doc);
    mutate(next);
    set({ doc: next });
  },

  endTransient: () => {
    const { transientBase, doc, past } = get();
    if (!transientBase || !doc) return;
    if (JSON.stringify(transientBase) === JSON.stringify(doc)) {
      set({ transientBase: null });
      return;
    }
    set({
      past: [...past, transientBase].slice(-HISTORY_LIMIT),
      future: [],
      transientBase: null,
      preview: null,
    });
  },

  cancelTransient: () => {
    const { transientBase } = get();
    if (transientBase) set({ doc: transientBase, transientBase: null });
  },

  undo: () => {
    const { past, future, doc } = get();
    if (!doc || past.length === 0) return;
    const previous = past[past.length - 1];
    set({ doc: previous, past: past.slice(0, -1), future: [doc, ...future], preview: null });
  },

  redo: () => {
    const { past, future, doc } = get();
    if (!doc || future.length === 0) return;
    const [next, ...rest] = future;
    set({ doc: next, past: [...past, doc], future: rest, preview: null });
  },

  markSaved: (planId, version) => {
    const { doc } = get();
    set({ planId, version, savedSnapshot: JSON.stringify(doc) });
  },

  restoreVersion: (doc) => {
    const { doc: current, past } = get();
    if (!current) return;
    set({ doc: clone(doc), past: [...past, current].slice(-HISTORY_LIMIT), future: [], preview: null, selection: null });
  },

  select: (selection) => set({ selection }),
  highlight: (ids) => set({ highlighted: ids }),
  setTool: (tool, placingType = null) => set({ tool, placingType }),
  setMode: (mode) =>
    set((state) => ({
      mode,
      tool: 'select',
      placingType: null,
      preview: null,
      highlighted: [],
      activeFinding: null,
      panel:
        mode === 'review'
          ? 'review'
          : mode === 'operate'
            ? 'passport'
            : state.panel === 'passport' || state.panel === 'review'
              ? 'checks'
              : state.panel,
    })),
  setView: (view) => set({ view }),
  setPanel: (panel) => set({ panel }),
  setHeatmap: (heatmap) => set({ heatmap }),
  setReport: (report, reportState) => set({ report, reportState }),
  setPreview: (preview) => set({ preview }),
  setActiveFinding: (activeFinding) => set({ activeFinding }),
  latch: (ids) => {
    set({ latched: ids });
    window.setTimeout(() => set({ latched: [] }), 700);
  },

  updateEquipment: (id, patch) =>
    get().commit((draft) => {
      const item = draft.equipment?.find((e) => e.id === id);
      if (item) transformInPlace(item, patch);
    }),

  updateZone: (id, patch) =>
    get().commit((draft) => {
      const zone = draft.zones?.find((z) => z.id === id);
      if (zone) Object.assign(zone, patch);
    }),

  removeSelection: () => {
    const { selection } = get();
    if (!selection) return;
    get().commit((draft) => {
      if (selection.kind === 'equipment') {
        draft.equipment = (draft.equipment ?? []).filter((e) => e.id !== selection.id);
      } else if (selection.kind === 'zone') {
        draft.zones = (draft.zones ?? []).filter((z) => z.id !== selection.id);
        (draft.equipment ?? []).forEach((e) => {
          if (e.zone === selection.id) delete e.zone;
        });
      } else if (selection.kind === 'wall') {
        const next = removeWall(draft, selection.id);
        draft.walls = next.walls;
        draft.doors = next.doors;
        draft.windows = next.windows;
      } else if (selection.kind === 'window') {
        draft.windows = (draft.windows ?? []).filter((w) => w.id !== selection.id);
      } else if (selection.kind === 'door') {
        draft.doors = (draft.doors ?? []).filter((d) => d.id !== selection.id);
      }
    });
    set({ selection: null });
  },

  /** То же, что делает сервер при применении рекомендации: add, move, remove. */
  applyChanges: (changes) =>
    get().commit((draft) => {
      let equipment = draft.equipment ?? [];
      for (const change of changes) {
        if (change.op === 'remove') {
          equipment = equipment.filter((e) => e.id !== change.object_id);
          continue;
        }
        let item = equipment.find((e) => e.id === change.object_id);
        if (!item) {
          item = {
            id: change.object_id,
            type: change.equipment_type,
            label: change.label ?? undefined,
            position: change.position ?? [0, 0],
            size_m: change.size_m ?? [1, 1],
          };
          equipment.push(item);
        }
        if (change.position) item.position = change.position;
        if (change.rotation_deg !== null && change.rotation_deg !== undefined) {
          item.rotation_deg = change.rotation_deg;
        }
        if (change.size_m) item.size_m = change.size_m;
        if (change.operator_position) item.operator_position = change.operator_position;
        // Зона берётся по новому месту, а не переносится следом за объектом: иначе скамья,
        // уехавшая из зоны ожидания, остаётся к ней привязанной, и план сам себе противоречит.
        const zone = change.zone ?? zoneAt(draft, item.position)?.id;
        if (zone) item.zone = zone;
        else delete item.zone;
      }
      draft.equipment = equipment;
    }),
}));

export const isDirty = (state: WorkspaceState): boolean =>
  state.doc !== null && JSON.stringify(state.doc) !== state.savedSnapshot;
