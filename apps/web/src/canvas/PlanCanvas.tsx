/**
 * Холст плана.
 *
 * Лист плана рисуется в SVG: так у каждого объекта есть собственный элемент, по которому
 * ловится указатель, а подписи и адреса остаются чёткими при любом масштабе. Геометрия
 * пересчитывается из метров в пиксели одним видом (lib/geometry).
 */

import { memo, useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react';
import type { AddressBook } from '../lib/addresses';
import { key } from '../lib/addresses';
import {
  distanceToSegment,
  equipmentAt,
  fitView,
  magnetToWalls,
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
import type { Equipment, EquipmentTypeInfo, Plan, Point, RecommendationChange } from '../lib/plan';
import { equipmentCorners, planBounds } from '../lib/plan';
import { formatNumber } from '../lib/planFacts';
import { outlinePoints } from '../lib/planFacts';
import { useWorkspace } from '../state/store';
import { glyphFor } from './glyphs';
import './canvas.css';

export interface Layers {
  zones: boolean;
  labels: boolean;
  routes: boolean;
  grid: boolean;
}

interface PlanCanvasProps {
  types: Map<string, EquipmentTypeInfo>;
  book: AddressBook;
  layers: Layers;
  fitSignal: number;
  focus: { id: string; nonce: number } | null;
  onViewChange: (view: View) => void;
  onCursor: (point: Point | null) => void;
}

const POSITION_STEP = 0.05;
const DRAFT_STEP = 0.25;
const ROTATE_STEP = 15;
const OUTER_WALL_M = 0.2;
const PARTITION_M = 0.1;

type Gesture =
  | { kind: 'none' }
  | { kind: 'pan'; startX: number; startY: number; origin: View }
  | { kind: 'drag'; id: string; startWorld: Point; startPosition: Point; moved: boolean }
  | { kind: 'rotate'; id: string };

const ZONE_FILL: Record<string, string> = {
  client_area: 'var(--zone-client)',
  waiting: 'var(--zone-waiting)',
  queue_accumulator: 'var(--zone-queue)',
  vestibule: 'var(--zone-client)',
  back_office: 'url(#hatch-back)',
  staff_room: 'url(#hatch-back)',
};

function PlanCanvasInner({ types, book, layers, fitSignal, focus, onViewChange, onCursor }: PlanCanvasProps) {
  const doc = useWorkspace((s) => s.doc);
  const selection = useWorkspace((s) => s.selection);
  const highlighted = useWorkspace((s) => s.highlighted);
  const tool = useWorkspace((s) => s.tool);
  const placingType = useWorkspace((s) => s.placingType);
  const preview = useWorkspace((s) => s.preview);
  const latched = useWorkspace((s) => s.latched);
  const mode = useWorkspace((s) => s.mode);
  const select = useWorkspace((s) => s.select);
  const commit = useWorkspace((s) => s.commit);
  const beginTransient = useWorkspace((s) => s.beginTransient);
  const transient = useWorkspace((s) => s.transient);
  const endTransient = useWorkspace((s) => s.endTransient);
  const setTool = useWorkspace((s) => s.setTool);

  const container = useRef<HTMLDivElement>(null);
  const svg = useRef<SVGSVGElement>(null);
  const [size, setSize] = useState({ width: 800, height: 600 });
  const [view, setView] = useState<View>({ scale: 60, originX: 60, originY: 540 });
  const [cursor, setCursor] = useState<Point | null>(null);
  const [hoverId, setHoverId] = useState<string | null>(null);
  const [zoneDraft, setZoneDraft] = useState<Point[]>([]);
  const [wallStart, setWallStart] = useState<Point | null>(null);
  const gesture = useRef<Gesture>({ kind: 'none' });
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
  }, [tool]);

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        setZoneDraft([]);
        setWallStart(null);
      }
      if (event.key === 'Enter' && zoneDraft.length >= 3) closeZone(zoneDraft);
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

  const onPointerDown = (event: React.PointerEvent<SVGSVGElement>) => {
    if (!doc) return;
    const { sx, sy } = pointer(event);
    const world = toWorld(view, sx, sy);
    svg.current?.setPointerCapture(event.pointerId);

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
      probe.position = magnetToWalls(doc, probe);
      const zone = zoneAt(doc, probe.position);
      if (zone) probe.zone = zone.id;
      if (info?.capacity_persons) probe.capacity_persons = info.capacity_persons;
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

    // Выбор и перетаскивание
    if (selection?.kind === 'equipment' && mode === 'design') {
      const item = doc.equipment?.find((e) => e.id === selection.id);
      if (item) {
        const handle = rotationHandle(view, item);
        if (Math.hypot(handle.knob[0] - sx, handle.knob[1] - sy) < 10) {
          beginTransient();
          gesture.current = { kind: 'rotate', id: item.id };
          return;
        }
      }
    }

    const item = equipmentAt(doc, world);
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
      const door = doc.doors?.find((d) => distanceToSegment(world, [d.wall[0], d.wall[1]], [d.wall[2], d.wall[3]]) < tolerance);
      if (door) {
        select({ kind: 'door', id: door.id });
        return;
      }
      const wall = doc.walls?.find((w) => distanceToSegment(world, w.from, w.to) < tolerance);
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
    setCursor(world);
    onCursor(world);
    const current = gesture.current;

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
        target.position = [
          round3(snap(current.startPosition[0] + dx, step)),
          round3(snap(current.startPosition[1] + dy, step)),
        ];
        target.position = magnetToWalls(draft, target);
        const zone = zoneAt(draft, target.position);
        if (zone) target.zone = zone.id;
        else delete target.zone;
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
        target.rotation_deg = ((Math.round(degrees / step) * step) % 360 + 360) % 360;
      });
      return;
    }

    if (mode === 'design' && tool === 'select') {
      setHoverId(equipmentAt(doc, world)?.id ?? null);
    }
  };

  const onPointerUp = (event: React.PointerEvent<SVGSVGElement>) => {
    svg.current?.releasePointerCapture(event.pointerId);
    const current = gesture.current;
    if (current.kind === 'drag' || current.kind === 'rotate') endTransient();
    gesture.current = { kind: 'none' };
  };

  const onWheel = (event: React.WheelEvent<SVGSVGElement>) => {
    const { sx, sy } = pointer(event);
    setView((v) => zoomAt(v, sx, sy, Math.pow(1.0016, -event.deltaY)));
  };

  const onDoubleClick = () => {
    if (tool === 'zone' && zoneDraft.length >= 3) closeZone(zoneDraft);
  };

  const outline = useMemo(() => (doc ? outlinePoints(doc) : null), [doc]);
  const interior = useMemo<Point | null>(() => (outline ? polygonCentroid(outline) : null), [outline]);

  if (!doc) return <div ref={container} className="plan-canvas" />;

  const highlightSet = new Set(highlighted);
  const labelsVisible = layers.labels && view.scale >= 34;

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

        {/* Стены */}
        {(doc.walls ?? []).map((wall) => {
          const [ax, ay] = toScreen(view, wall.from);
          const [bx, by] = toScreen(view, wall.to);
          const outer = (wall.type ?? 'outer') === 'outer';
          const selected = selection?.kind === 'wall' && selection.id === wall.id;
          return (
            <line
              key={wall.id}
              x1={ax}
              y1={ay}
              x2={bx}
              y2={by}
              stroke={selected ? 'var(--notice)' : 'var(--ink)'}
              strokeWidth={Math.max((outer ? OUTER_WALL_M : PARTITION_M) * view.scale, outer ? 3 : 2)}
              strokeLinecap="square"
            />
          );
        })}

        {/* Окна */}
        {(doc.windows ?? []).map((win) => {
          const [ax, ay] = toScreen(view, [win.wall[0], win.wall[1]]);
          const [bx, by] = toScreen(view, [win.wall[2], win.wall[3]]);
          const thickness = Math.max(OUTER_WALL_M * view.scale, 3);
          const nx = -(by - ay);
          const ny = bx - ax;
          const length = Math.hypot(nx, ny) || 1;
          const ox = (nx / length) * (thickness / 3.2);
          const oy = (ny / length) * (thickness / 3.2);
          return (
            <g key={win.id} className="plan-window">
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
              {(labelsVisible || isHighlighted) && (
                <AddressTag
                  x={Math.min(...xs)}
                  y={Math.min(...ys) - 4}
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
            const [lx, ly] = toScreen(view, polygonCentroid(zone.polygon));
            return (
              <g key={`label-${zone.id}`} className="plan-zone-label" transform={`translate(${lx} ${ly})`}>
                <AddressTag x={-18} y={-10} text={book.byId.get(key('zone', zone.id)) ?? ''} tone="steel" />
                <text x={0} y={18} textAnchor="middle">
                  {zone.label ?? zone.type}
                </text>
              </g>
            );
          })}

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
        {tool === 'equipment' && placingType && cursor && (
          <PlacingGhost view={view} doc={doc} type={placingType} info={types.get(placingType)} cursor={cursor} />
        )}

        <ScaleBar view={view} height={size.height} />
      </svg>
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

function AddressTag({ x, y, text, tone }: { x: number; y: number; text: string; tone: 'ink' | 'signal' | 'steel' }) {
  if (!text) return null;
  const width = 7 * text.length + 8;
  return (
    <g className={`plan-address plan-address--${tone}`} transform={`translate(${x} ${y - 15})`}>
      <rect width={width} height={15} rx={1.5} />
      <text x={width / 2} y={11} textAnchor="middle">
        {text}
      </text>
    </g>
  );
}

function DoorGlyph({
  view,
  segment,
  interior,
  entrance,
  selected,
}: {
  view: View;
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
  const cross = (sbx - sax) * (sly - say) - (sby - say) * (slx - sax);
  const sweep = cross > 0 ? 1 : 0;
  return (
    <g className={`plan-door${entrance ? ' plan-door--entrance' : ''}${selected ? ' plan-door--selected' : ''}`}>
      <line x1={sax} y1={say} x2={sbx} y2={sby} className="plan-door__gap" strokeWidth={Math.max(OUTER_WALL_M * view.scale, 3) + 2} />
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
  ghost.position = magnetToWalls(doc, ghost);
  const [gx, gy] = toScreen(view, ghost.position);
  const Glyph = glyphFor(type);
  return (
    <g className="plan-placing" pointerEvents="none">
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
