/**
 * Холст плана.
 *
 * Лист плана рисуется в SVG: так у каждого объекта есть собственный элемент, по которому
 * ловится указатель, а подписи и адреса остаются чёткими при любом масштабе. Геометрия
 * пересчитывается из метров в пиксели одним видом (lib/geometry).
 */

import { AppWindow, DoorOpen } from 'lucide-react';
import { memo, useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react';
import type { AddressBook } from '../lib/addresses';
import { formatAddress, key } from '../lib/addresses';
import {
  distanceToSegment,
  equipmentAt,
  fitView,
  polygonCentroid,
  round3,
  screenPath,
  snap,
  toScreen,
  toWorld,
  uniqueId,
  zoneAt,
  zoomAt,
  type View,
} from '../lib/geometry';
import type { Equipment, EquipmentTypeInfo, Plan, Point, RecommendationChange, ValidationReport, Wall } from '../lib/plan';
import { equipmentCorners, planBounds } from '../lib/plan';
import { formatNumber } from '../lib/planFacts';
import { outlinePoints } from '../lib/planFacts';
import { defaultOperatorPosition, transformInPlace } from '../lib/equipmentOps';
import { MIN_WALL_M, addOpening, moveWall, replaceOutline, wallAxis, wallLength } from '../lib/wallEdit';
import {
  OUTER_WALL_M,
  PARTITION_M,
  alignToNeighbours,
  fitsRoom,
  hostWall,
  isOuter,
  offsetOutline,
  outwardNormal,
  placeInRoom,
  type Guide,
} from '../lib/room';
import { zoneLabelPoints } from '../lib/zoneLabels';
import { inspectionState } from '../lib/inspections';
import { useWorkspace, type Selection } from '../state/store';
import { glyphFor } from './glyphs';
import './canvas.css';

export interface Layers {
  zones: boolean;
  labels: boolean;
  routes: boolean;
  grid: boolean;
  /** Тепловая карта по записи с камеры; появляется в списке слоёв только когда расчёт сделан. */
  heat: boolean;
  /** Метки нарушений в редакторе. В обзоре они есть всегда: там это главный рассказ экрана. */
  findings: boolean;
}

interface PlanCanvasProps {
  types: Map<string, EquipmentTypeInfo>;
  book: AddressBook;
  layers: Layers;
  fitSignal: number;
  focus: { id: string; nonce: number } | null;
  onViewChange: (view: View) => void;
  onCursor: (point: Point | null) => void;
  onNotice?: (text: string) => void;
}

const POSITION_STEP = 0.05;
// Насколько близко к линии соседа объект должен подойти, чтобы встать на неё, — в пикселях
// экрана: на любом масштабе это одно и то же расстояние под курсором.
const ALIGN_TOLERANCE_PX = 7;
const DRAFT_STEP = 0.25;
/** На сколько пикселей экрана касание может промахнуться мимо объекта. */
const TOUCH_SLOP_PX = 12;
const ROTATE_STEP = 15;

type Gesture =
  | { kind: 'none' }
  | { kind: 'pan'; startX: number; startY: number; origin: View }
  // Щипок двумя пальцами: масштаб от расстояния между ними, сдвиг — от их середины.
  | { kind: 'pinch'; startGap: number; startCenter: Point; origin: View }
  | { kind: 'drag'; id: string; startWorld: Point; startPosition: Point; moved: boolean }
  | { kind: 'rotate'; id: string }
  | { kind: 'wall'; id: string; axis: 'x' | 'y'; startWorld: Point; base: Plan; delta: number };

interface WallMenu {
  wallId: string;
  point: Point;
  sx: number;
  sy: number;
}

const ZONE_FILL: Record<string, string> = {
  client_area: 'var(--zone-client)',
  waiting: 'var(--zone-waiting)',
  queue_accumulator: 'var(--zone-queue)',
  vestibule: 'var(--zone-client)',
  back_office: 'url(#hatch-back)',
  staff_room: 'url(#hatch-back)',
};

/** Шкала тепловой карты: редко — холодная синь, часто — сигнальный красный. Цвета из палитры. */
const HEAT_RAMP: [number, number, number][] = [
  [44, 90, 116],
  [184, 137, 58],
  [179, 57, 31],
];

function heatColour(value: number): [number, number, number, number] {
  if (value < 0.05) return [0, 0, 0, 0];
  // Степень меньше единицы поднимает средние значения: следы прохода видны, а ядра скоплений
  // остаются самыми насыщенными.
  const weight = Math.min(1, value) ** 0.6;
  const position = weight * (HEAT_RAMP.length - 1);
  const low = Math.min(HEAT_RAMP.length - 2, Math.floor(position));
  const mix = position - low;
  const channel = (index: number) =>
    Math.round(HEAT_RAMP[low][index] + (HEAT_RAMP[low + 1][index] - HEAT_RAMP[low][index]) * mix);
  return [channel(0), channel(1), channel(2), Math.round((0.4 + weight * 0.55) * 255)];
}

function PlanCanvasInner({ types, book, layers, fitSignal, focus, onViewChange, onCursor, onNotice }: PlanCanvasProps) {
  const doc = useWorkspace((s) => s.doc);
  const heatmap = useWorkspace((s) => s.heatmap);
  const selection = useWorkspace((s) => s.selection);
  const highlighted = useWorkspace((s) => s.highlighted);
  const tool = useWorkspace((s) => s.tool);
  const placingType = useWorkspace((s) => s.placingType);
  const preview = useWorkspace((s) => s.preview);
  const latched = useWorkspace((s) => s.latched);
  const mode = useWorkspace((s) => s.mode);
  const report = useWorkspace((s) => s.report);
  const reportState = useWorkspace((s) => s.reportState);
  const setPanel = useWorkspace((s) => s.setPanel);
  const activeFinding = useWorkspace((s) => s.activeFinding);
  const setActiveFinding = useWorkspace((s) => s.setActiveFinding);
  const highlight = useWorkspace((s) => s.highlight);
  const select = useWorkspace((s) => s.select);
  const commit = useWorkspace((s) => s.commit);
  const beginTransient = useWorkspace((s) => s.beginTransient);
  const transient = useWorkspace((s) => s.transient);
  const endTransient = useWorkspace((s) => s.endTransient);
  const setTool = useWorkspace((s) => s.setTool);

  // Карта рисуется картинкой в клетку ячейки и растягивается на план: браузер сглаживает её
  // сам, и вместо лесенки из квадратов получается поле, по которому видно скопление.
  const heatImage = useMemo(() => {
    if (!heatmap || !heatmap.values.length) return null;
    const canvas = document.createElement('canvas');
    canvas.width = heatmap.cols;
    canvas.height = heatmap.rows;
    const context = canvas.getContext('2d');
    if (!context) return null;
    const image = context.createImageData(heatmap.cols, heatmap.rows);
    // Шкала делится не по наибольшей ячейке, а по верхнему проценту: одна горячая точка иначе
    // прижимает весь остальной зал к нулю, и карта выглядит пустой.
    const filled = heatmap.values.filter((value) => value > 0.02).sort((a, b) => a - b);
    const top = filled.length ? filled[Math.min(filled.length - 1, Math.floor(filled.length * 0.92))] : 1;
    for (let index = 0; index < heatmap.values.length; index += 1) {
      const value = Math.max(0, Math.min(1, heatmap.values[index] / Math.max(top, 0.05)));
      const col = index % heatmap.cols;
      // Строки карты идут снизу вверх, у картинки — сверху вниз.
      const row = heatmap.rows - 1 - Math.floor(index / heatmap.cols);
      const at = (row * heatmap.cols + col) * 4;
      const [red, green, blue, alpha] = heatColour(value);
      image.data[at] = red;
      image.data[at + 1] = green;
      image.data[at + 2] = blue;
      image.data[at + 3] = alpha;
    }
    context.putImageData(image, 0, 0);
    return canvas.toDataURL();
  }, [heatmap]);

  const container = useRef<HTMLDivElement>(null);
  const svg = useRef<SVGSVGElement>(null);
  const [size, setSize] = useState({ width: 800, height: 600 });
  const [view, setView] = useState<View>({ scale: 60, originX: 60, originY: 540 });
  const [cursor, setCursor] = useState<Point | null>(null);
  const [hoverId, setHoverId] = useState<string | null>(null);
  const [zoneDraft, setZoneDraft] = useState<Point[]>([]);
  const [wallStart, setWallStart] = useState<Point | null>(null);
  const [outlineDraft, setOutlineDraft] = useState<Point[]>([]);
  const [wallMenu, setWallMenu] = useState<WallMenu | null>(null);
  const [wallShift, setWallShift] = useState<number | null>(null);
  const gesture = useRef<Gesture>({ kind: 'none' });
  // Касания на плане: по двум из них считается щипок. Мышь сюда попадает тоже, но одна.
  const touches = useRef<Map<number, Point>>(new Map());
  // Линии выравнивания: показываются, пока объект тянут, и гаснут, когда отпустили.
  const [guides, setGuides] = useState<Guide[]>([]);
  const fitted = useRef(false);

  useLayoutEffect(() => {
    const element = container.current;
    if (!element) return;
    const observer = new ResizeObserver(([entry]) => {
      setSize({ width: entry.contentRect.width, height: entry.contentRect.height });
    });
    observer.observe(element);
    return () => observer.disconnect();
  }, []);

  const fit = useCallback(() => {
    if (!doc) return;
    setView(fitView(planBounds(doc), size.width, size.height));
  }, [doc, size.width, size.height]);

  useEffect(() => {
    if (doc && !fitted.current && size.width > 0) {
      fitted.current = true;
      fit();
    }
  }, [doc, fit, size.width]);

  useEffect(() => {
    if (fitSignal > 0) fit();
    // Вписывание только по явной команде.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [fitSignal]);

  useEffect(() => {
    if (!focus || !doc) return;
    const item = doc.equipment?.find((e) => e.id === focus.id);
    const zone = doc.zones?.find((z) => z.id === focus.id);
    const target: Point | null = item ? item.position : zone ? polygonCentroid(zone.polygon) : null;
    if (!target) return;
    setView((v) => {
      const scale = Math.max(v.scale, 70);
      return { scale, originX: size.width / 2 - target[0] * scale, originY: size.height / 2 + target[1] * scale };
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [focus?.nonce]);

  useEffect(() => onViewChange(view), [view, onViewChange]);

  useEffect(() => {
    if (tool !== 'zone') setZoneDraft([]);
    if (tool !== 'wall') setWallStart(null);
    if (tool !== 'outline') setOutlineDraft([]);
    setWallMenu(null);
  }, [tool]);

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        setZoneDraft([]);
        setWallStart(null);
        setOutlineDraft([]);
        setWallMenu(null);
      }
      if (event.key === 'Enter' && zoneDraft.length >= 3) closeZone(zoneDraft);
      if (event.key === 'Enter' && outlineDraft.length >= 3) closeOutline(outlineDraft);
      if (event.key === 'Backspace' && outlineDraft.length > 0 && tool === 'outline') {
        event.preventDefault();
        setOutlineDraft((points) => points.slice(0, -1));
      }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  });

  const pointer = (event: React.PointerEvent | React.WheelEvent | React.MouseEvent) => {
    const rect = svg.current!.getBoundingClientRect();
    return { sx: event.clientX - rect.left, sy: event.clientY - rect.top };
  };

  const closeZone = (points: Point[]) => {
    const zoneId = uniqueId((doc?.zones ?? []).map((z) => z.id), 'z');
    commit((draft) => {
      draft.zones = [
        ...(draft.zones ?? []),
        { id: zoneId, type: 'client_area', label: 'Новая зона', polygon: points.map(([x, y]) => [round3(x), round3(y)]) },
      ];
    });
    setZoneDraft([]);
    setTool('select');
    select({ kind: 'zone', id: zoneId });
  };

  const closeOutline = (points: Point[]) => {
    if (!doc) return;
    const { plan: next, droppedOpenings } = replaceOutline(doc, points);
    commit((draft) => {
      draft.walls = next.walls;
      draft.doors = next.doors;
      draft.windows = next.windows;
    });
    setOutlineDraft([]);
    setTool('select');
    onNotice?.(
      droppedOpenings > 0
        ? `Контур заменён. Проёмов, оставшихся без стены, удалено: ${droppedOpenings}. Отменить — Ctrl+Z`
        : 'Контур заменён. Границы зон не менялись — проверьте их. Отменить — Ctrl+Z',
    );
  };

  const addOpeningAt = (kind: 'door' | 'window') => {
    if (!doc || !wallMenu) return;
    const result = addOpening(doc, wallMenu.wallId, wallMenu.point, kind);
    setWallMenu(null);
    if ('error' in result) {
      onNotice?.(result.error);
      return;
    }
    commit((draft) => {
      draft.doors = result.plan.doors;
      draft.windows = result.plan.windows;
      draft.flow_hints = result.plan.flow_hints;
    });
    select({ kind: kind === 'door' ? 'door' : 'window', id: result.id });
  };

  /** Ручка сдвига осевой стены — на середине стены, там, где стена нарисована. */
  const wallHandle = (wall: Wall): [number, number] | null => {
    if (!doc || !wallAxis(wall)) return null;
    const outer = isOuter(wall);
    const [nx, ny] = outer ? outwardNormal(doc, wall, outlinePoints(doc)) : [0, 0];
    const shift = outer ? OUTER_WALL_M / 2 : 0;
    return toScreen(view, [(wall.from[0] + wall.to[0]) / 2 + nx * shift, (wall.from[1] + wall.to[1]) / 2 + ny * shift]);
  };

  /** Середина и расстояние между двумя касаниями — основа щипка. */
  const twoFingers = (): { center: Point; gap: number } | null => {
    const points = [...touches.current.values()];
    if (points.length < 2) return null;
    const [first, second] = points;
    return {
      center: [(first[0] + second[0]) / 2, (first[1] + second[1]) / 2],
      gap: Math.max(Math.hypot(first[0] - second[0], first[1] - second[1]), 1),
    };
  };

  const onPointerDown = (event: React.PointerEvent<SVGSVGElement>) => {
    if (!doc) return;
    const { sx, sy } = pointer(event);
    const world = toWorld(view, sx, sy);
    svg.current?.setPointerCapture(event.pointerId);
    setWallMenu(null);
    touches.current.set(event.pointerId, [sx, sy]);

    // Второй палец переводит жест в щипок: начатое перетаскивание при этом откатывается,
    // иначе объект уезжал бы вместе с масштабированием.
    const fingers = twoFingers();
    if (fingers) {
      const started = gesture.current;
      if (started.kind === 'drag' || started.kind === 'rotate' || started.kind === 'wall') endTransient();
      gesture.current = { kind: 'pinch', startGap: fingers.gap, startCenter: fingers.center, origin: view };
      return;
    }

    if (event.button === 1 || (event.button === 0 && event.altKey)) {
      gesture.current = { kind: 'pan', startX: sx, startY: sy, origin: view };
      return;
    }
    if (event.button !== 0) return;

    if (mode === 'design' && tool === 'equipment' && placingType) {
      const info = types.get(placingType);
      const size_m = info?.default_size_m ?? [1, 1];
      const id = uniqueId((doc.equipment ?? []).map((e) => e.id), placingType);
      const probe: Equipment = {
        id,
        type: placingType,
        label: info?.label,
        position: [round3(snap(world[0], POSITION_STEP)), round3(snap(world[1], POSITION_STEP))],
        size_m,
        rotation_deg: 0,
      };
      const floor = info?.floor_object ?? true;
      probe.position = placeInRoom(doc, probe, null, floor);
      if (floor && !fitsRoom(doc, probe)) {
        onNotice?.('Здесь объект не помещается: он заходит в стену или за контур помещения.');
        return;
      }
      const zone = zoneAt(doc, probe.position);
      if (zone) probe.zone = zone.id;
      if (info?.capacity_persons) probe.capacity_persons = info.capacity_persons;
      if (info?.operator_workplace) probe.operator_position = defaultOperatorPosition(doc, probe);
      commit((draft) => {
        draft.equipment = [...(draft.equipment ?? []), probe];
      });
      select({ kind: 'equipment', id });
      if (!event.shiftKey) setTool('select');
      return;
    }

    if (mode === 'design' && tool === 'zone') {
      const point: Point = [snap(world[0], DRAFT_STEP), snap(world[1], DRAFT_STEP)];
      if (zoneDraft.length >= 3) {
        const [fx, fy] = toScreen(view, zoneDraft[0]);
        if (Math.hypot(fx - sx, fy - sy) < 12) {
          closeZone(zoneDraft);
          return;
        }
      }
      setZoneDraft((points) => [...points, point]);
      return;
    }

    if (mode === 'design' && tool === 'outline') {
      let point: Point = event.shiftKey ? [snap(world[0], POSITION_STEP), snap(world[1], POSITION_STEP)] : [snap(world[0], DRAFT_STEP), snap(world[1], DRAFT_STEP)];
      const last = outlineDraft[outlineDraft.length - 1];
      if (last && !event.shiftKey) {
        point = Math.abs(point[0] - last[0]) >= Math.abs(point[1] - last[1]) ? [point[0], last[1]] : [last[0], point[1]];
      }
      if (outlineDraft.length >= 3) {
        const [fx, fy] = toScreen(view, outlineDraft[0]);
        if (Math.hypot(fx - sx, fy - sy) < 14) {
          closeOutline(outlineDraft);
          return;
        }
      }
      if (last && Math.hypot(point[0] - last[0], point[1] - last[1]) < MIN_WALL_M) return;
      setOutlineDraft((points) => [...points, [round3(point[0]), round3(point[1])]]);
      return;
    }

    if (mode === 'design' && tool === 'wall') {
      let point: Point = [snap(world[0], DRAFT_STEP), snap(world[1], DRAFT_STEP)];
      if (!wallStart) {
        setWallStart(point);
        return;
      }
      if (!event.shiftKey) {
        const dx = Math.abs(point[0] - wallStart[0]);
        const dy = Math.abs(point[1] - wallStart[1]);
        point = dx >= dy ? [point[0], wallStart[1]] : [wallStart[0], point[1]];
      }
      if (Math.hypot(point[0] - wallStart[0], point[1] - wallStart[1]) < 0.2) return;
      const wallId = uniqueId((doc.walls ?? []).map((w) => w.id), 'w_partition');
      commit((draft) => {
        draft.walls = [
          ...(draft.walls ?? []),
          { id: wallId, from: wallStart, to: point, type: 'partition', label: 'Перегородка' },
        ];
      });
      setWallStart(null);
      select({ kind: 'wall', id: wallId });
      return;
    }

    // Ручка выделенной стены
    if (selection?.kind === 'wall' && mode === 'design') {
      const wall = doc.walls?.find((w) => w.id === selection.id);
      const handle = wall ? wallHandle(wall) : null;
      const axis = wall ? wallAxis(wall) : null;
      if (wall && handle && axis && Math.hypot(handle[0] - sx, handle[1] - sy) < 13) {
        beginTransient();
        gesture.current = { kind: 'wall', id: wall.id, axis, startWorld: world, base: doc, delta: 0 };
        setWallShift(0);
        return;
      }
    }

    // Выбор и перетаскивание
    if (selection?.kind === 'equipment' && mode === 'design') {
      const item = doc.equipment?.find((e) => e.id === selection.id);
      if (item) {
        const handle = rotationHandle(view, item);
        if (Math.hypot(handle.knob[0] - sx, handle.knob[1] - sy) < KNOB_GRAB_PX) {
          beginTransient();
          gesture.current = { kind: 'rotate', id: item.id };
          return;
        }
      }
    }

    // Касание пальцем ловит и мелкий объект рядом: на телефоне шкаф бывает меньше пальца.
    const item = equipmentAt(doc, world, event.pointerType === 'touch' ? TOUCH_SLOP_PX / view.scale : 0);
    if (item) {
      select({ kind: 'equipment', id: item.id });
      if (mode === 'design') {
        beginTransient();
        gesture.current = { kind: 'drag', id: item.id, startWorld: world, startPosition: item.position, moved: false };
      }
      return;
    }

    if (mode === 'design') {
      const tolerance = 8 / view.scale;
      const nearOpening = (segment: [number, number, number, number]) =>
        distanceToSegment(world, [segment[0], segment[1]], [segment[2], segment[3]]) < tolerance + OUTER_WALL_M;
      const door = doc.doors?.find((d) => nearOpening(d.wall));
      if (door) {
        select({ kind: 'door', id: door.id });
        return;
      }
      const win = doc.windows?.find((w) => nearOpening(w.wall));
      if (win) {
        select({ kind: 'window', id: win.id });
        return;
      }
      const wall = doc.walls?.find((w) => distanceToSegment(world, w.from, w.to) < tolerance + (isOuter(w) ? OUTER_WALL_M : 0));
      if (wall) {
        select({ kind: 'wall', id: wall.id });
        return;
      }
    }

    const zone = layers.zones ? zoneAt(doc, world) : null;
    if (zone && mode === 'design') {
      select({ kind: 'zone', id: zone.id });
      gesture.current = { kind: 'pan', startX: sx, startY: sy, origin: view };
      return;
    }

    select(null);
    gesture.current = { kind: 'pan', startX: sx, startY: sy, origin: view };
  };

  const onPointerMove = (event: React.PointerEvent<SVGSVGElement>) => {
    if (!doc) return;
    const { sx, sy } = pointer(event);
    const world = toWorld(view, sx, sy);
    if (touches.current.has(event.pointerId)) touches.current.set(event.pointerId, [sx, sy]);
    setCursor(world);
    onCursor(world);
    const current = gesture.current;

    if (current.kind === 'pinch') {
      const fingers = twoFingers();
      if (!fingers) return;
      const [cx, cy] = current.startCenter;
      const zoomed = zoomAt(current.origin, cx, cy, fingers.gap / current.startGap);
      setView({
        scale: zoomed.scale,
        originX: zoomed.originX + (fingers.center[0] - cx),
        originY: zoomed.originY + (fingers.center[1] - cy),
      });
      return;
    }

    if (current.kind === 'pan') {
      setView({
        scale: current.origin.scale,
        originX: current.origin.originX + (sx - current.startX),
        originY: current.origin.originY + (sy - current.startY),
      });
      return;
    }

    if (current.kind === 'drag') {
      const dx = world[0] - current.startWorld[0];
      const dy = world[1] - current.startWorld[1];
      if (!current.moved && Math.hypot(dx, dy) * view.scale < 3) return;
      current.moved = true;
      transient((draft) => {
        const target = draft.equipment?.find((e) => e.id === current.id);
        if (!target) return;
        const step = event.shiftKey ? DRAFT_STEP : POSITION_STEP;
        const previous = target.position;
        const probe: Equipment = {
          ...target,
          position: [round3(snap(current.startPosition[0] + dx, step)), round3(snap(current.startPosition[1] + dy, step))],
        };
        // Ctrl отключает привязку к стенам и соседям, но не пускает сквозь стены.
        const floor = types.get(target.type)?.floor_object ?? true;
        let next = event.ctrlKey
          ? fitsRoom(draft, probe) || !floor
            ? probe.position
            : previous
          : placeInRoom(draft, probe, previous, floor);
        // Выравнивание по соседям — поверх прижатия к стенам и только если объект там помещается.
        let lines: Guide[] = [];
        if (!event.ctrlKey) {
          const aligned = alignToNeighbours(draft, { ...target, position: next }, ALIGN_TOLERANCE_PX / view.scale);
          if (aligned.guides.length && (!floor || fitsRoom(draft, { ...target, position: aligned.position }))) {
            next = aligned.position;
            lines = aligned.guides;
          }
        }
        setGuides(lines);
        transformInPlace(target, { position: next });
        const zone = zoneAt(draft, target.position);
        if (zone) target.zone = zone.id;
        else delete target.zone;
      });
      return;
    }

    if (current.kind === 'wall') {
      const index = current.axis === 'x' ? 0 : 1;
      const step = event.shiftKey ? DRAFT_STEP : POSITION_STEP;
      const delta = round3(snap(world[index] - current.startWorld[index], step));
      if (delta === current.delta) return;
      const moved = moveWall(current.base, current.id, delta);
      // Стена, которая схлопнула бы соседнюю, останавливается на последнем допустимом месте.
      if (!moved) return;
      current.delta = delta;
      setWallShift(delta);
      transient((draft) => {
        draft.walls = moved.walls;
        draft.doors = moved.doors;
        draft.windows = moved.windows;
        draft.zones = moved.zones;
      });
      return;
    }

    if (current.kind === 'rotate') {
      transient((draft) => {
        const target = draft.equipment?.find((e) => e.id === current.id);
        if (!target) return;
        const [cx, cy] = toScreen(view, target.position);
        // Угол от центра к указателю; 0° — ручка над объектом, поворот по часовой.
        const degrees = (Math.atan2(sx - cx, cy - sy) * 180) / Math.PI;
        const step = event.shiftKey ? 1 : ROTATE_STEP;
        const rotation = ((Math.round(degrees / step) * step) % 360 + 360) % 360;
        // Поворот, при котором объект упирается в стену, не применяется.
        if ((types.get(target.type)?.floor_object ?? true) && !fitsRoom(draft, { ...target, rotation_deg: rotation })) return;
        transformInPlace(target, { rotation_deg: rotation });
      });
      return;
    }

    if (mode === 'design' && tool === 'select') {
      setHoverId(equipmentAt(doc, world)?.id ?? null);
    }
  };

  const onPointerUp = (event: React.PointerEvent<SVGSVGElement>) => {
    svg.current?.releasePointerCapture(event.pointerId);
    touches.current.delete(event.pointerId);
    setGuides([]);
    const current = gesture.current;
    if (current.kind === 'drag' || current.kind === 'rotate' || current.kind === 'wall') endTransient();
    if (current.kind === 'wall') setWallShift(null);
    // Пока на плане остаётся второй палец, жест ещё не кончился.
    if (current.kind === 'pinch' && touches.current.size > 0) return;
    gesture.current = { kind: 'none' };
  };

  const onWheel = (event: React.WheelEvent<SVGSVGElement>) => {
    const { sx, sy } = pointer(event);
    setView((v) => zoomAt(v, sx, sy, Math.pow(1.0016, -event.deltaY)));
  };

  const onDoubleClick = (event: React.MouseEvent<SVGSVGElement>) => {
    if (tool === 'zone' && zoneDraft.length >= 3) {
      closeZone(zoneDraft);
      return;
    }
    if (!doc || mode !== 'design' || tool !== 'select') return;
    const { sx, sy } = pointer(event);
    const world = toWorld(view, sx, sy);
    const tolerance = 8 / view.scale;
    const wall = doc.walls?.find((w) => distanceToSegment(world, w.from, w.to) < tolerance + (isOuter(w) ? OUTER_WALL_M : 0));
    if (wall) setWallMenu({ wallId: wall.id, point: world, sx, sy });
  };

  const outline = useMemo(() => (doc ? outlinePoints(doc) : null), [doc]);
  const interior = useMemo<Point | null>(() => (outline ? polygonCentroid(outline) : null), [outline]);
  // Во время перетаскивания подписи зон не двигаются: точки считаются по плану до начала жеста.
  const transientBase = useWorkspace((s) => s.transientBase);
  const labelSource = transientBase ?? doc;
  const labelPoints = useMemo(
    () => (labelSource ? zoneLabelPoints(labelSource) : new Map<string, Point>()),
    [labelSource],
  );

  if (!doc) return <div ref={container} className="plan-canvas" />;

  const highlightSet = new Set(highlighted);
  const labelsVisible = layers.labels && view.scale >= 34;
  // В обзоре коды-адреса не нужны: объекты узнаются по значкам, нарушения — по номерам меток.
  const review = mode === 'review';
  const addressesVisible = labelsVisible && !review;
  // Таблички адресов раскладываются так, чтобы не налезать друг на друга и на названия зон.
  const tags = placeTags(doc, view, book, {
    visible: (item) => addressesVisible || (highlightSet.has(item.id) && !review),
    zones: layers.zones && labelsVisible ? labelPoints : null,
    zoneTags: addressesVisible,
  });
  // В обзоре метки крупные и есть всегда. В редакторе это тихий слой: маленький номер у места
  // нарушения — тот же, что в адресе Н-03 в «Проверке», — который не мешает двигать объекты.
  const design = mode === 'design';
  const pins =
    report && !preview && (review || design)
      ? placePins(doc, report, activeFinding, view, {
          style: review ? 'review' : 'design',
          all: review || layers.findings,
          labelPoints: labelsVisible && layers.zones ? labelPoints : null,
          // Метка не должна закрывать адресные таблички, по которым объект ищут, и ручки выделения.
          avoid: design ? pinObstacles(doc, view, selection, [...tags.values()]) : [],
        })
      : [];

  return (
    <div
      ref={container}
      className={`plan-canvas plan-canvas--tool-${tool} plan-canvas--${mode}`}
      data-placing={placingType ? 'true' : undefined}
    >
      <svg
        ref={svg}
        width={size.width}
        height={size.height}
        role="application"
        aria-label="План отделения. Колесо — масштаб, перетаскивание пустого места — сдвиг."
        onPointerDown={onPointerDown}
        onPointerMove={onPointerMove}
        onPointerUp={onPointerUp}
        onPointerCancel={onPointerUp}
        onPointerLeave={() => {
          setCursor(null);
          onCursor(null);
          setHoverId(null);
        }}
        onWheel={onWheel}
        onDoubleClick={onDoubleClick}
      >
        <defs>
          <pattern
            id="grid-minor"
            width={0.25 * view.scale}
            height={0.25 * view.scale}
            patternUnits="userSpaceOnUse"
            x={view.originX}
            y={view.originY}
          >
            <path
              d={`M${0.25 * view.scale} 0H0V${0.25 * view.scale}`}
              fill="none"
              stroke="var(--sheet-grid)"
              strokeWidth="1"
            />
          </pattern>
          <pattern
            id="grid-major"
            width={view.scale}
            height={view.scale}
            patternUnits="userSpaceOnUse"
            x={view.originX}
            y={view.originY}
          >
            {0.25 * view.scale >= 7 && <rect width={view.scale} height={view.scale} fill="url(#grid-minor)" />}
            <path d={`M${view.scale} 0H0V${view.scale}`} fill="none" stroke="var(--sheet-grid-major)" strokeWidth="1" />
          </pattern>
          <pattern id="hatch-back" width="9" height="9" patternUnits="userSpaceOnUse" patternTransform="rotate(45)">
            <rect width="9" height="9" fill="rgba(43,51,54,0.035)" />
            <line x1="0" y1="0" x2="0" y2="9" stroke="rgba(43,51,54,0.22)" strokeWidth="1" />
          </pattern>
          <pattern id="hatch-signal" width="7" height="7" patternUnits="userSpaceOnUse" patternTransform="rotate(-45)">
            <rect width="7" height="7" fill="rgba(179,57,31,0.08)" />
            <line x1="0" y1="0" x2="0" y2="7" stroke="rgba(179,57,31,0.55)" strokeWidth="1.4" />
          </pattern>
          <marker id="drawer-arrow" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="9" markerHeight="9" orient="auto-start-reverse">
            <path d="M0 0L10 5L0 10z" fill="var(--ink)" />
          </marker>
        </defs>

        <rect width={size.width} height={size.height} fill="var(--sheet)" />
        {layers.grid && <rect width={size.width} height={size.height} fill="url(#grid-major)" />}

        {outline && <path d={screenPath(view, outline)} fill="var(--sheet)" fillOpacity="0.55" />}

        {/* Зоны */}
        {layers.zones &&
          (doc.zones ?? []).map((zone) => {
            const selected = selection?.kind === 'zone' && selection.id === zone.id;
            return (
              <path
                key={zone.id}
                d={screenPath(view, zone.polygon)}
                fill={ZONE_FILL[zone.type] ?? 'var(--zone-client)'}
                stroke={selected ? 'var(--ink)' : 'var(--zone-edge)'}
                strokeWidth={selected ? 2 : 1}
                strokeDasharray={selected ? undefined : '6 4'}
                className="plan-zone"
              />
            );
          })}

        {/* Тепловая карта посещаемости: где посетители стояли чаще всего */}
        {layers.heat && heatmap && heatImage && (() => {
          const [sx, sy] = toScreen(view, [
            heatmap.origin[0],
            heatmap.origin[1] + heatmap.rows * heatmap.cell_m,
          ]);
          return (
            <>
              {/* Карта обрезается контуром: посетители ходят по полу, а не по стенам. */}
              {outline && (
                <clipPath id="heat-floor">
                  <path d={screenPath(view, outline)} />
                </clipPath>
              )}
              <image
                className="plan-heat"
                aria-hidden="true"
                href={heatImage}
                x={sx}
                y={sy}
                width={heatmap.cols * heatmap.cell_m * view.scale}
                height={heatmap.rows * heatmap.cell_m * view.scale}
                preserveAspectRatio="none"
                clipPath={outline ? 'url(#heat-floor)' : undefined}
              />
            </>
          );
        })()}

        {/* Маршруты посетителей */}
        {layers.routes &&
          (doc.flow_hints?.typical_paths ?? []).map((path, index) => (
            <g key={`route-${index}`} className="plan-route">
              <path d={screenPath(view, path.waypoints, false)} fill="none" />
              {path.frequency_share !== undefined && (
                <text {...labelAt(view, path.waypoints[path.waypoints.length - 1], 8, -8)}>
                  {Math.round(path.frequency_share * 100)}%
                </text>
              )}
            </g>
          ))}

        {/* Стены: наружная задана внутренней гранью, толщина лежит снаружи помещения */}
        {outline && (
          <path
            d={`${screenPath(view, outline)}${screenPath(view, offsetOutline(outline, OUTER_WALL_M))}`}
            fillRule="evenodd"
            fill="var(--ink)"
            className="plan-walls"
          />
        )}
        {(doc.walls ?? []).map((wall) => {
          const outer = isOuter(wall);
          // Замкнутый контур уже нарисован кольцом; отдельно — перегородки, разомкнутые стены и выделение.
          const selected = selection?.kind === 'wall' && selection.id === wall.id;
          if (outer && outline && !selected) return null;
          const thickness = outer ? OUTER_WALL_M : PARTITION_M;
          const [nx, ny] = outer ? outwardNormal(doc, wall, outline) : [0, 0];
          const shift = outer ? thickness / 2 : 0;
          const [ax, ay] = toScreen(view, [wall.from[0] + nx * shift, wall.from[1] + ny * shift]);
          const [bx, by] = toScreen(view, [wall.to[0] + nx * shift, wall.to[1] + ny * shift]);
          return (
            <line
              key={wall.id}
              x1={ax}
              y1={ay}
              x2={bx}
              y2={by}
              stroke={selected ? 'var(--notice)' : 'var(--ink)'}
              strokeWidth={Math.max(thickness * view.scale, outer ? 3 : 2)}
              strokeLinecap={outer ? 'butt' : 'square'}
            />
          );
        })}

        {/* Окна */}
        {(doc.windows ?? []).map((win) => {
          const host = hostWall(doc, win.wall);
          const outer = host ? isOuter(host) : true;
          const thicknessM = outer ? OUTER_WALL_M : PARTITION_M;
          const [wx, wy] = host && outer ? outwardNormal(doc, host, outline) : [0, 0];
          const shift = outer ? thicknessM / 2 : 0;
          const [ax, ay] = toScreen(view, [win.wall[0] + wx * shift, win.wall[1] + wy * shift]);
          const [bx, by] = toScreen(view, [win.wall[2] + wx * shift, win.wall[3] + wy * shift]);
          const thickness = Math.max(thicknessM * view.scale, 3);
          const nx = -(by - ay);
          const ny = bx - ax;
          const length = Math.hypot(nx, ny) || 1;
          const ox = (nx / length) * (thickness / 3.2);
          const oy = (ny / length) * (thickness / 3.2);
          const selectedWindow = selection?.kind === 'window' && selection.id === win.id;
          return (
            <g key={win.id} className={`plan-window${selectedWindow ? ' plan-window--selected' : ''}`}>
              <line x1={ax} y1={ay} x2={bx} y2={by} stroke="var(--sheet)" strokeWidth={thickness - 1} />
              <line x1={ax + ox} y1={ay + oy} x2={bx + ox} y2={by + oy} />
              <line x1={ax - ox} y1={ay - oy} x2={bx - ox} y2={by - oy} />
            </g>
          );
        })}

        {/* Дверные проёмы */}
        {(doc.doors ?? []).map((door) => (
          <DoorGlyph
            key={door.id}
            view={view}
            plan={doc}
            outline={outline}
            segment={door.wall}
            interior={interior}
            entrance={door.type === 'entrance'}
            selected={selection?.kind === 'door' && selection.id === door.id}
          />
        ))}

        {/* Оборудование */}
        {(doc.equipment ?? []).map((item) => {
          const info = types.get(item.type);
          const floor = info?.floor_object ?? true;
          const corners = equipmentCorners(item);
          const screen = corners.map((p) => toScreen(view, p));
          const xs = screen.map((p) => p[0]);
          const ys = screen.map((p) => p[1]);
          const w = Math.max(...xs) - Math.min(...xs);
          const h = Math.max(...ys) - Math.min(...ys);
          const [cx, cy] = toScreen(view, item.position);
          const Glyph = glyphFor(item.type);
          const glyphSize = Math.max(10, Math.min(Math.min(w, h) * 0.62, 26));
          const isHighlighted = highlightSet.has(item.id);
          const isPreviewed = preview?.some((change) => change.object_id === item.id && change.op !== 'add');
          const isHover = hoverId === item.id && !(selection?.kind === 'equipment' && selection.id === item.id);
          // В «Эксплуатации» план — паспорт отделения: у объекта видно, что подходит срок осмотра.
          const due = mode === 'operate' ? inspectionState(item.inventory?.next_inspection) : null;
          return (
            <g
              key={item.id}
              className={`plan-equipment${floor ? '' : ' plan-equipment--mounted'}${isPreviewed ? ' plan-equipment--drawn-out' : ''}${isHover ? ' plan-equipment--hover' : ''}${latched.includes(item.id) ? ' plan-equipment--latched' : ''}`}
            >
              <path d={screenPath(view, corners)} className="plan-equipment__body" />
              {isHighlighted && <path d={screenPath(view, corners)} className="plan-equipment__violation" />}
              {item.operator_position && floor && (
                <circle
                  cx={toScreen(view, item.operator_position)[0]}
                  cy={toScreen(view, item.operator_position)[1]}
                  r={Math.max(0.18 * view.scale, 3)}
                  className="plan-operator"
                />
              )}
              {glyphSize >= 10 && (
                <Glyph
                  x={cx - glyphSize / 2}
                  y={cy - glyphSize / 2}
                  width={glyphSize}
                  height={glyphSize}
                  strokeWidth={1.75}
                  className="plan-equipment__glyph"
                  aria-hidden="true"
                />
              )}
              {due && (due.tone === 'overdue' || due.tone === 'soon') && (
                <circle cx={Math.max(...xs)} cy={Math.max(...ys)} r={5} className={`plan-due plan-due--${due.tone}`}>
                  <title>Осмотр: {due.relative}</title>
                </circle>
              )}
              {tags.has(item.id) && (
                <AddressTag
                  x={tags.get(item.id)!.x0}
                  y={tags.get(item.id)!.y1}
                  text={book.byId.get(key('equipment', item.id)) ?? ''}
                  tone={isHighlighted ? 'signal' : 'ink'}
                />
              )}
            </g>
          );
        })}

        {/* Подписи зон */}
        {layers.zones &&
          labelsVisible &&
          (doc.zones ?? []).map((zone) => {
            const [lx, ly] = toScreen(view, labelPoints.get(zone.id) ?? polygonCentroid(zone.polygon));
            return (
              <g key={`label-${zone.id}`} className="plan-zone-label" transform={`translate(${lx} ${ly})`}>
                {addressesVisible && (
                  <AddressTag x={-18} y={-10} text={book.byId.get(key('zone', zone.id)) ?? ''} tone="steel" />
                )}
                <text x={0} y={addressesVisible ? 18 : 4} textAnchor="middle">
                  {zone.label ?? zone.type}
                </text>
              </g>
            );
          })}

        {/* Метки нарушений: номер совпадает с пунктом списка в обзоре и с адресом в «Проверке» */}
        {pins.length > 0 && (
          <g className={`plan-pins${reportState === 'checking' ? ' plan-pins--stale' : ''}`}>
            {pins.map((pin) => (
              <g
                key={pin.index}
                className={`plan-pin${pin.active ? ' plan-pin--active' : ''}${pin.note ? ' plan-pin--note' : ''}${design && !pin.active ? ' plan-pin--quiet' : ''}`}
                transform={`translate(${pin.x} ${pin.y})`}
                role="button"
                aria-pressed={pin.active}
                aria-label={
                  design
                    ? `${pin.note ? 'Замечание' : 'Нарушение'} ${formatAddress('finding', pin.index)}`
                    : pin.number === null
                      ? 'Замечание'
                      : `Нарушение ${pin.number}`
                }
                onPointerDown={(event) => event.stopPropagation()}
                onClick={() => {
                  if (pin.active) {
                    setActiveFinding(null);
                    highlight([]);
                    return;
                  }
                  setActiveFinding(pin.index);
                  highlight(pin.objects);
                  // В редакторе подробности нарушения — в «Проверке»: там же закреплённая строка.
                  if (design) setPanel('checks');
                }}
              >
                <circle r={design && !pin.active ? 12 : 16} className="plan-pin__hit" />
                <g className="plan-pin__body">
                  <circle r={pin.active ? 15 : design ? 9 : 12} className="plan-pin__disc" />
                  <text textAnchor="middle" dominantBaseline="central" className="plan-pin__number">
                    {pin.number ?? '!'}
                  </text>
                </g>
              </g>
            ))}
          </g>
        )}

        {/* Предпросмотр рекомендации: объект выдвигается, как ящик ячейки */}
        {preview && <PreviewLayer view={view} doc={doc} changes={preview} types={types} />}

        {/* Выделение */}
        {selection?.kind === 'equipment' &&
          (() => {
            const item = doc.equipment?.find((e) => e.id === selection.id);
            return item ? <SelectionFrame view={view} item={item} editable={mode === 'design'} /> : null;
          })()}

        {/* Черновики инструментов */}
        {tool === 'zone' && zoneDraft.length > 0 && (
          <g className="plan-draft">
            <path
              d={screenPath(
                view,
                cursor ? [...zoneDraft, [snap(cursor[0], DRAFT_STEP), snap(cursor[1], DRAFT_STEP)]] : zoneDraft,
                false,
              )}
            />
            {zoneDraft.map((p, index) => {
              const [x, y] = toScreen(view, p);
              return <rect key={index} x={x - 3.5} y={y - 3.5} width={7} height={7} />;
            })}
          </g>
        )}
        {tool === 'wall' && wallStart && cursor && (
          <WallDraft view={view} start={wallStart} cursor={cursor} />
        )}
        {tool === 'outline' && outlineDraft.length > 0 && (
          <OutlineDraft view={view} points={outlineDraft} cursor={cursor} />
        )}

        {/* Ручка сдвига выделенной стены */}
        {mode === 'design' &&
          selection?.kind === 'wall' &&
          (() => {
            const wall = doc.walls?.find((w) => w.id === selection.id);
            const handle = wall ? wallHandle(wall) : null;
            if (!wall || !handle) return null;
            const vertical = wallAxis(wall) === 'x';
            const [hx, hy] = handle;
            return (
              <g className="plan-wall-handle" transform={`translate(${hx} ${hy})`}>
                <circle r={10} />
                <path d={vertical ? 'M-6 0H6M-6 0l3-3M-6 0l3 3M6 0l-3-3M6 0l-3 3' : 'M0 -6V6M0 -6l-3 3M0 -6l3 3M0 6l-3-3M0 6l3-3'} />
                <text x={vertical ? 16 : 0} y={vertical ? 4 : -16} textAnchor={vertical ? 'start' : 'middle'} className="plan-wall-handle__label">
                  {wallShift !== null
                    ? `${wallShift > 0 ? '+' : wallShift < 0 ? '−' : ''}${formatNumber(Math.abs(wallShift))} м`
                    : `${formatNumber(wallLength(wall))} м`}
                </text>
              </g>
            );
          })()}
        {tool === 'equipment' && placingType && cursor && (
          <PlacingGhost view={view} doc={doc} type={placingType} info={types.get(placingType)} cursor={cursor} />
        )}

        {guides.map((guide) => {
          const [ax, ay] = toScreen(view, guide.axis === 'x' ? [guide.at, guide.from] : [guide.from, guide.at]);
          const [bx, by] = toScreen(view, guide.axis === 'x' ? [guide.at, guide.to] : [guide.to, guide.at]);
          return <line key={`${guide.axis}${guide.at}`} className="plan-guide" x1={ax} y1={ay} x2={bx} y2={by} />;
        })}

        <ScaleBar view={view} height={size.height} />
      </svg>
      {wallMenu && (
        <div className="wall-menu" role="menu" aria-label="Проём в стене" style={{ left: wallMenu.sx, top: wallMenu.sy }}>
          <button type="button" role="menuitem" onClick={() => addOpeningAt('door')}>
            <DoorOpen size={15} aria-hidden="true" />
            Дверь
          </button>
          {doc.walls?.find((w) => w.id === wallMenu.wallId && isOuter(w)) && (
            <button type="button" role="menuitem" onClick={() => addOpeningAt('window')}>
              <AppWindow size={15} aria-hidden="true" />
              Окно
            </button>
          )}
        </div>
      )}
    </div>
  );
}

export const PlanCanvas = memo(PlanCanvasInner);

function labelAt(view: View, point: Point, dx: number, dy: number) {
  const [x, y] = toScreen(view, point);
  return { x: x + dx, y: y + dy };
}

function rotationHandle(view: View, item: Equipment) {
  const [cx, cy] = toScreen(view, item.position);
  const angle = ((item.rotation_deg ?? 0) * Math.PI) / 180;
  const reach = (item.size_m[1] / 2) * view.scale + 22;
  return {
    base: [cx + Math.sin(angle) * ((item.size_m[1] / 2) * view.scale), cy - Math.cos(angle) * ((item.size_m[1] / 2) * view.scale)] as [number, number],
    knob: [cx + Math.sin(angle) * reach, cy - Math.cos(angle) * reach] as [number, number],
  };
}

const TAG_HEIGHT = 15;
const tagWidth = (text: string) => 7 * text.length + 8;

interface TagLayout {
  visible: (item: Equipment) => boolean;
  /** Точки подписей зон, если подписи видны: таблички объектов их не закрывают. */
  zones: Map<string, Point> | null;
  /** Видны ли таблички зон над их названиями. */
  zoneTags: boolean;
}

/** Ширина буквы подписи зоны в 12 px: для оценки, какое место название занимает на листе. */
const ZONE_LABEL_CHAR_PX = 6.6;

/**
 * Таблички адресов не налезают друг на друга: если над объектом тесно, табличка встаёт над
 * его правым краем, под ним или сбоку. Объекты обходятся в порядке плана, поэтому при каждой
 * отрисовке раскладка одна и та же.
 */
function placeTags(doc: Plan, view: View, book: AddressBook, layout: TagLayout): Map<string, Box> {
  const taken: Box[] = [];
  if (layout.zones) {
    for (const zone of doc.zones ?? []) {
      const point = layout.zones.get(zone.id);
      if (!point) continue;
      const [lx, ly] = toScreen(view, point);
      const name = zone.label ?? zone.type;
      const half = (name.length * ZONE_LABEL_CHAR_PX) / 2;
      if (layout.zoneTags) {
        const text = book.byId.get(key('zone', zone.id)) ?? '';
        taken.push({ x0: lx - 18, y0: ly - 10 - TAG_HEIGHT, x1: lx - 18 + tagWidth(text), y1: ly - 10 });
        taken.push({ x0: lx - half, y0: ly + 6, x1: lx + half, y1: ly + 21 });
      } else {
        taken.push({ x0: lx - half, y0: ly - 8, x1: lx + half, y1: ly + 7 });
      }
    }
  }
  const placed = new Map<string, Box>();
  for (const item of doc.equipment ?? []) {
    if (!layout.visible(item)) continue;
    const text = book.byId.get(key('equipment', item.id)) ?? '';
    if (!text) continue;
    const screen = equipmentCorners(item).map((corner) => toScreen(view, corner));
    const left = Math.min(...screen.map((c) => c[0]));
    const right = Math.max(...screen.map((c) => c[0]));
    const top = Math.min(...screen.map((c) => c[1]));
    const bottom = Math.max(...screen.map((c) => c[1]));
    const width = tagWidth(text);
    const at = (x: number, y: number): Box => ({ x0: x, y0: y, x1: x + width, y1: y + TAG_HEIGHT });
    const candidates = [
      at(left, top - 4 - TAG_HEIGHT),
      at(right - width, top - 4 - TAG_HEIGHT),
      at(left, bottom + 4),
      at(right - width, bottom + 4),
      at(right + 4, top),
      at(left - width - 4, top),
    ];
    const box = candidates.find((candidate) => !taken.some((other) => boxesTouch(candidate, other))) ?? candidates[0];
    placed.set(item.id, box);
    taken.push(box);
  }
  return placed;
}

/** Прямоугольники ближе 2 px друг к другу: таблички читаются только с просветом. */
function boxesTouch(a: Box, b: Box): boolean {
  const gap = 2;
  return a.x0 < b.x1 + gap && b.x0 < a.x1 + gap && a.y0 < b.y1 + gap && b.y0 < a.y1 + gap;
}

function AddressTag({ x, y, text, tone }: { x: number; y: number; text: string; tone: 'ink' | 'signal' | 'steel' }) {
  if (!text) return null;
  const width = tagWidth(text);
  return (
    <g className={`plan-address plan-address--${tone}`} transform={`translate(${x} ${y - TAG_HEIGHT})`}>
      <rect width={width} height={TAG_HEIGHT} rx={1.5} />
      <text x={width / 2} y={11} textAnchor="middle">
        {text}
      </text>
    </g>
  );
}

function DoorGlyph({
  view,
  plan,
  outline,
  segment,
  interior,
  entrance,
  selected,
}: {
  view: View;
  plan: Plan;
  outline: Point[] | null;
  segment: [number, number, number, number];
  interior: Point | null;
  entrance: boolean;
  selected: boolean;
}) {
  const a: Point = [segment[0], segment[1]];
  const b: Point = [segment[2], segment[3]];
  const length = Math.hypot(b[0] - a[0], b[1] - a[1]);
  if (length < 1e-6) return null;
  const ux = (b[0] - a[0]) / length;
  const uy = (b[1] - a[1]) / length;
  let nx = -uy;
  let ny = ux;
  if (interior) {
    const mx = (a[0] + b[0]) / 2;
    const my = (a[1] + b[1]) / 2;
    if ((interior[0] - mx) * nx + (interior[1] - my) * ny < 0) {
      nx = -nx;
      ny = -ny;
    }
  }
  const leafEnd: Point = [a[0] + nx * length, a[1] + ny * length];
  const [sax, say] = toScreen(view, a);
  const [sbx, sby] = toScreen(view, b);
  const [slx, sly] = toScreen(view, leafEnd);
  const radius = length * view.scale;
  const host = hostWall(plan, segment);
  const outer = host ? isOuter(host) : true;
  const thickness = outer ? OUTER_WALL_M : PARTITION_M;
  const [ox, oy] = host && outer ? outwardNormal(plan, host, outline) : [0, 0];
  const shift = outer ? thickness / 2 : 0;
  const [gax, gay] = toScreen(view, [a[0] + ox * shift, a[1] + oy * shift]);
  const [gbx, gby] = toScreen(view, [b[0] + ox * shift, b[1] + oy * shift]);
  const cross = (sbx - sax) * (sly - say) - (sby - say) * (slx - sax);
  const sweep = cross > 0 ? 1 : 0;
  return (
    <g className={`plan-door${entrance ? ' plan-door--entrance' : ''}${selected ? ' plan-door--selected' : ''}`}>
      <line x1={gax} y1={gay} x2={gbx} y2={gby} className="plan-door__gap" strokeWidth={Math.max(thickness * view.scale, 3) + 2} />
      <line x1={sax} y1={say} x2={slx} y2={sly} className="plan-door__leaf" />
      <path d={`M${sbx} ${sby}A${radius} ${radius} 0 0 ${sweep} ${slx} ${sly}`} className="plan-door__swing" />
    </g>
  );
}

function SelectionFrame({ view, item, editable }: { view: View; item: Equipment; editable: boolean }) {
  const corners = equipmentCorners(item).map((p) => toScreen(view, p));
  const handle = rotationHandle(view, item);
  const [cx] = toScreen(view, item.position);
  const ys = corners.map((p) => p[1]);
  return (
    <g className="plan-selection" pointerEvents="none">
      <path d={`M${corners.map((p) => p.join(' ')).join('L')}Z`} className="plan-selection__outline" />
      {editable && (
        <>
          {corners.map(([x, y], index) => (
            <rect key={index} x={x - 3.5} y={y - 3.5} width={7} height={7} className="plan-selection__corner" />
          ))}
          <line x1={handle.base[0]} y1={handle.base[1]} x2={handle.knob[0]} y2={handle.knob[1]} className="plan-selection__stem" />
          <circle cx={handle.knob[0]} cy={handle.knob[1]} r={5.5} className="plan-selection__knob" />
        </>
      )}
      <text x={cx} y={Math.max(...ys) + 18} textAnchor="middle" className="plan-selection__size">
        {formatNumber(item.size_m[0])} × {formatNumber(item.size_m[1])} м
        {item.rotation_deg ? ` · ${Math.round(item.rotation_deg)}°` : ''}
      </text>
    </g>
  );
}

function PreviewLayer({
  view,
  doc,
  changes,
  types,
}: {
  view: View;
  doc: Plan;
  changes: RecommendationChange[];
  types: Map<string, EquipmentTypeInfo>;
}) {
  return (
    <g className="plan-preview" pointerEvents="none">
      {changes.map((change, index) => {
        const existing = doc.equipment?.find((e) => e.id === change.object_id);
        if (change.op === 'remove') {
          if (!existing) return null;
          const [cx, cy] = toScreen(view, existing.position);
          return (
            <g key={index} className="plan-preview__remove">
              <path d={screenPath(view, equipmentCorners(existing))} />
              <path d={`M${cx - 9} ${cy - 9}L${cx + 9} ${cy + 9}M${cx + 9} ${cy - 9}L${cx - 9} ${cy + 9}`} />
            </g>
          );
        }
        if (!change.position) return null;
        const ghost: Equipment = {
          id: change.object_id,
          type: change.equipment_type,
          position: change.position,
          size_m: change.size_m ?? existing?.size_m ?? types.get(change.equipment_type)?.default_size_m ?? [1, 1],
          rotation_deg: change.rotation_deg ?? existing?.rotation_deg ?? 0,
        };
        const [gx, gy] = toScreen(view, ghost.position);
        const from = change.previous_position ?? existing?.position ?? null;
        const Glyph = glyphFor(ghost.type);
        const start = from ? toScreen(view, from) : null;
        const dx = start ? start[0] - gx : 0;
        const dy = start ? start[1] - gy : 0;
        return (
          <g key={index}>
            {start && (
              <path
                d={`M${start[0]} ${start[1]}Q${(start[0] + gx) / 2 + dy * 0.18} ${(start[1] + gy) / 2 - dx * 0.18} ${gx} ${gy}`}
                className="plan-preview__path"
                markerEnd="url(#drawer-arrow)"
              />
            )}
            <g
              className="plan-preview__ghost"
              style={{ '--from-x': `${dx}px`, '--from-y': `${dy}px`, animationDelay: `${index * 90}ms` } as React.CSSProperties}
            >
              <path d={screenPath(view, equipmentCorners(ghost))} />
              <Glyph x={gx - 10} y={gy - 10} width={20} height={20} strokeWidth={1.75} aria-hidden="true" />
            </g>
          </g>
        );
      })}
    </g>
  );
}

interface Pin {
  index: number;
  /** Номер на метке; null — замечание в обзоре, где нумеруются только нарушения. */
  number: number | null;
  /** Замечание, а не нарушение: метка другого цвета. */
  note: boolean;
  objects: string[];
  active: boolean;
  x: number;
  y: number;
}

interface PinOptions {
  /** Обзор: метка в середине объектов. Редактор: у одиночного объекта — на его углу. */
  style: 'review' | 'design';
  /** Показывать все нарушения; иначе только закреплённое. */
  all: boolean;
  /** Точки подписей зон, если подписи видны: метку зоны нельзя ставить на её название. */
  labelPoints: Map<string, Point> | null;
  /** Что метка обходит, если есть куда: адресные таблички и ручки выделенного объекта. */
  avoid: Box[];
}

interface Box {
  x0: number;
  y0: number;
  x1: number;
  y1: number;
}

const PIN_GAP_PX = 28;
/** На каком расстоянии от ручки поворота щелчок её берёт. */
const KNOB_GRAB_PX = 10;
/** Радиус, который метка держит свободным. Один для тихой и выбранной: при щелчке метка не прыгает. */
const PIN_CLEAR_PX = 11;
/** Метка зоны встаёт под её название: подпись занимает около двух строк над точкой. */
const ZONE_PIN_DROP_PX = 34;

/**
 * Метка ставится туда, где возникло нарушение: для пары объектов — в середину между ними, это
 * и есть место конфликта. В редакторе метка одиночного объекта садится на его нижний правый
 * угол, как бейдж: объект остаётся под рукой, его можно взять и передвинуть. Метки, упавшие
 * друг на друга, раздвигаются по горизонтали.
 */
function placePins(doc: Plan, report: ValidationReport, active: number | null, view: View, options: PinOptions): Pin[] {
  const pins: Pin[] = [];
  let errorNumber = 0;
  report.findings.forEach((finding, index) => {
    const isError = finding.severity === 'error';
    if (isError) errorNumber += 1;
    if (!(isError && options.all) && index !== active) return;
    const candidates = pinAnchors(doc, finding.objects, view, options);
    if (candidates.length === 0) return;
    const free = (point: Point) =>
      !pins.some((pin) => Math.hypot(pin.x - point[0], pin.y - point[1]) < PIN_GAP_PX) &&
      !options.avoid.some((box) => discHitsBox(point, PIN_CLEAR_PX, box));
    let [x, y] = candidates.find(free) ?? candidates[0];
    while (pins.some((pin) => Math.hypot(pin.x - x, pin.y - y) < PIN_GAP_PX)) x += PIN_GAP_PX;
    // В редакторе номер — тот же, что в адресе нарушения; в обзоре нумеруются только нарушения,
    // и так как они идут первыми, номера совпадают.
    const number = options.style === 'design' ? index + 1 : isError ? errorNumber : null;
    pins.push({ index, number, note: !isError, objects: finding.objects, active: index === active, x, y });
  });
  // Выбранная метка рисуется последней, чтобы её не перекрывали соседние.
  return pins.sort((a, b) => Number(a.active) - Number(b.active));
}

/** Таблички адресов на листе и ручка поворота с подписью размера у выделенного объекта. */
function pinObstacles(doc: Plan, view: View, selection: Selection | null, tags: Box[]): Box[] {
  const boxes: Box[] = [...tags];
  const selected = selection?.kind === 'equipment' ? doc.equipment?.find((e) => e.id === selection.id) : undefined;
  if (selected) {
    const { base, knob } = rotationHandle(view, selected);
    const reach = KNOB_GRAB_PX;
    boxes.push({ x0: knob[0] - reach, y0: knob[1] - reach, x1: knob[0] + reach, y1: knob[1] + reach });
    boxes.push({
      x0: Math.min(base[0], knob[0]) - 2,
      y0: Math.min(base[1], knob[1]) - 2,
      x1: Math.max(base[0], knob[0]) + 2,
      y1: Math.max(base[1], knob[1]) + 2,
    });
    // Подпись размера под выделенным объектом.
    const corners = equipmentCorners(selected).map((corner) => toScreen(view, corner));
    const [cx] = toScreen(view, selected.position);
    const bottom = Math.max(...corners.map((c) => c[1]));
    boxes.push({ x0: cx - 48, y0: bottom + 7, x1: cx + 48, y1: bottom + 22 });
  }
  return boxes;
}

function discHitsBox([x, y]: Point, radius: number, box: Box): boolean {
  const nearestX = Math.max(box.x0, Math.min(x, box.x1));
  const nearestY = Math.max(box.y0, Math.min(y, box.y1));
  return Math.hypot(x - nearestX, y - nearestY) < radius;
}

/**
 * Места для метки в порядке предпочтения. Для одиночного объекта в редакторе — его углы и
 * бока, начиная с нижнего правого; для остальных — место конфликта и небольшие сдвиги от него.
 */
function pinAnchors(doc: Plan, objects: string[], view: View, options: PinOptions): Point[] {
  const anchor = pinAnchor(doc, objects, view, options);
  if (!anchor) return [];
  const items = objects.filter((id) => doc.equipment?.some((e) => e.id === id));
  if (options.style === 'design' && items.length === 1) {
    const item = doc.equipment!.find((e) => e.id === items[0])!;
    const corners = equipmentCorners(item).map((corner) => toScreen(view, corner));
    const left = Math.min(...corners.map((c) => c[0]));
    const right = Math.max(...corners.map((c) => c[0]));
    const top = Math.min(...corners.map((c) => c[1]));
    const bottom = Math.max(...corners.map((c) => c[1]));
    const middle = (top + bottom) / 2;
    // На боку метка стоит снаружи и только касается объекта: узкий объект она иначе закрыла бы.
    const beside = PIN_CLEAR_PX - 2;
    return [anchor, [left, bottom], [right + beside, middle], [left - beside, middle], [right, top]];
  }
  const step = PIN_CLEAR_PX * 1.6;
  const [x, y] = anchor;
  return [anchor, [x, y + step], [x, y - step], [x + step, y], [x - step, y]];
}

function pinAnchor(doc: Plan, objects: string[], view: View, options: PinOptions): Point | null {
  const items = objects
    .map((id) => doc.equipment?.find((e) => e.id === id))
    .filter((item): item is Equipment => Boolean(item));
  if (options.style === 'design' && items.length === 1) {
    const corners = equipmentCorners(items[0]).map((corner) => toScreen(view, corner));
    return [Math.max(...corners.map((c) => c[0])), Math.max(...corners.map((c) => c[1]))];
  }
  const points: Point[] = items.map((item) => item.position);
  let zoneOnly: Point | null = null;
  for (const id of objects) {
    const zone = doc.zones?.find((z) => z.id === id);
    if (!zone) continue;
    const centre = polygonCentroid(zone.polygon);
    points.push(centre);
    zoneOnly = options.labelPoints?.get(zone.id) ?? centre;
  }
  if (points.length === 0) return null;
  if (items.length === 0 && points.length === 1 && zoneOnly) {
    const [x, y] = toScreen(view, zoneOnly);
    return [x, options.labelPoints ? y + ZONE_PIN_DROP_PX : y];
  }
  const world: Point = [
    points.reduce((sum, p) => sum + p[0], 0) / points.length,
    points.reduce((sum, p) => sum + p[1], 0) / points.length,
  ];
  return toScreen(view, world);
}

function OutlineDraft({ view, points, cursor }: { view: View; points: Point[]; cursor: Point | null }) {
  const last = points[points.length - 1];
  let next: Point | null = null;
  if (cursor) {
    next = [snap(cursor[0], DRAFT_STEP), snap(cursor[1], DRAFT_STEP)];
    next = Math.abs(next[0] - last[0]) >= Math.abs(next[1] - last[1]) ? [next[0], last[1]] : [last[0], next[1]];
  }
  const chain = next ? [...points, next] : points;
  return (
    <g className="plan-draft plan-draft--outline">
      <path d={screenPath(view, chain, false)} />
      {chain.slice(1).map((point, index) => {
        const from = chain[index];
        const length = Math.hypot(point[0] - from[0], point[1] - from[1]);
        if (length < 0.01) return null;
        const [mx, my] = toScreen(view, [(point[0] + from[0]) / 2, (point[1] + from[1]) / 2]);
        return (
          <text key={index} x={mx + 6} y={my - 6} className="plan-draft__length">
            {formatNumber(length)} м
          </text>
        );
      })}
      {points.map((p, index) => {
        const [x, y] = toScreen(view, p);
        return <rect key={index} x={x - 4} y={y - 4} width={8} height={8} className={index === 0 && points.length >= 3 ? 'plan-draft__close' : undefined} />;
      })}
    </g>
  );
}

function WallDraft({ view, start, cursor }: { view: View; start: Point; cursor: Point }) {
  let end: Point = [snap(cursor[0], DRAFT_STEP), snap(cursor[1], DRAFT_STEP)];
  const dx = Math.abs(end[0] - start[0]);
  const dy = Math.abs(end[1] - start[1]);
  end = dx >= dy ? [end[0], start[1]] : [start[0], end[1]];
  const [ax, ay] = toScreen(view, start);
  const [bx, by] = toScreen(view, end);
  const length = Math.hypot(end[0] - start[0], end[1] - start[1]);
  return (
    <g className="plan-draft">
      <line x1={ax} y1={ay} x2={bx} y2={by} strokeWidth={Math.max(PARTITION_M * view.scale, 2)} />
      <text x={(ax + bx) / 2 + 8} y={(ay + by) / 2 - 8} className="plan-draft__length">
        {formatNumber(length)} м
      </text>
    </g>
  );
}

function PlacingGhost({
  view,
  doc,
  type,
  info,
  cursor,
}: {
  view: View;
  doc: Plan;
  type: string;
  info: EquipmentTypeInfo | undefined;
  cursor: Point;
}) {
  const ghost: Equipment = {
    id: '__ghost__',
    type,
    position: [snap(cursor[0], POSITION_STEP), snap(cursor[1], POSITION_STEP)],
    size_m: info?.default_size_m ?? [1, 1],
    rotation_deg: 0,
  };
  const floor = info?.floor_object ?? true;
  ghost.position = placeInRoom(doc, ghost, null, floor);
  const fits = !floor || fitsRoom(doc, ghost);
  const [gx, gy] = toScreen(view, ghost.position);
  const Glyph = glyphFor(type);
  return (
    <g className={`plan-placing${fits ? '' : ' plan-placing--blocked'}`} pointerEvents="none">
      <path d={screenPath(view, equipmentCorners(ghost))} />
      <Glyph x={gx - 10} y={gy - 10} width={20} height={20} strokeWidth={1.75} aria-hidden="true" />
    </g>
  );
}

function ScaleBar({ view, height }: { view: View; height: number }) {
  const meters = view.scale >= 90 ? 1 : view.scale >= 40 ? 2 : 5;
  const width = meters * view.scale;
  return (
    <g className="plan-scalebar" transform={`translate(20 ${height - 24})`} pointerEvents="none">
      <rect x={-6} y={-18} width={width + 12} height={28} rx={2} />
      <path d={`M0 0V-6M0 -3H${width}M${width} 0V-6M${width / 2} -3V-6`} />
      <text x={width / 2} y={-9} textAnchor="middle">
        {meters} м
      </text>
    </g>
  );
}
