"""Поиск места для объекта на плане.

Три ступени, от дешёвой к дорогой:
1. генерация — позиции с шагом сетки и два поворота внутри разрешённых для типа зон;
2. быстрый фильтр — чистая геометрия: объект внутри зоны, не пересекает оборудование и
   перегородки, не занимает площадку у двери, держит норму прохода до окон обслуживания;
3. полная проверка — лучшие по смыслу кандидаты проверяются всеми правилами, и остаются
   только те, что не добавляют ошибок.

Порядок кандидатов перед полной проверкой задаёт «оценка уместности»: окно тяготеет к
служебной зоне, стеллаж — к стене, терминал очереди — к входу. Дорогую ступень можно
сократить ускорителем, который заранее предсказывает исход полной проверки (см. accelerator).
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass, field
from typing import Callable

import numpy as np
import shapely
from shapely.geometry import LinearRing, LineString, Point, Polygon, box
from shapely.ops import nearest_points, unary_union
from shapely.strtree import STRtree

from app.dictionaries import EquipmentType, equipment_types, zone_types
from app.geometry import core, passability
from app.models import Equipment, Plan
from app.rules.catalog import rules_by_id
from app.assistant import accelerator, objective
from app.rules.engine import SERVICE_WINDOW_TYPES, VISITOR_TARGET_TYPES, validate

GRID_STEP = 0.25
DEFAULT_TOP_K = 24
WALL_MOUNT_TOLERANCE = 0.15


@dataclass
class Candidate:
    position: tuple[float, float]
    rotation_deg: float
    polygon: Polygon
    fitness: float
    operator_position: tuple[float, float] | None = None
    features: dict[str, float] = field(default_factory=dict)


@dataclass
class Evaluated:
    candidate: Candidate
    plan: Plan
    errors: int
    warnings: int
    min_route_width: float | None
    accepted: bool
    score: objective.Score | None = None


@dataclass
class SearchResult:
    equipment_type: str
    generated: int
    passed_fast_filter: int
    fully_checked: int
    elapsed_ms: float
    baseline_errors: int
    best: list[Evaluated]


def _walls(plan: Plan) -> list[LineString]:
    return [LineString([w.from_, w.to]) for w in plan.walls]


def _allowed_region(plan: Plan, type_def: EquipmentType, zone_id: str | None) -> Polygon | None:
    shape = core.outline(plan)
    if shape is None:
        return None
    if zone_id is not None:
        zones = [z for z in plan.zones if z.id == zone_id]
    else:
        zones = [z for z in plan.zones if z.type in type_def.allowed_zones]
    if not zones:
        # Если зон подходящего типа на плане нет, объект можно ставить в любой общедоступной зоне.
        public = {code for code, zt in zone_types(plan).items() if zt.public}
        zones = [z for z in plan.zones if z.type in public] if plan.zones else []
    region = unary_union([core.zone_polygon(z) for z in zones]) if zones else shape
    # Зоны на плане могут перекрываться: для размещения нужна только часть, которая не
    # принадлежит зонам другого назначения.
    if zone_id is None and zones:
        foreign = [
            core.zone_polygon(z)
            for z in plan.zones
            if z not in zones and z.type not in type_def.allowed_zones
        ]
        if foreign:
            region = region.difference(unary_union(foreign))
    return region.intersection(shape)


def _door_squares(plan: Plan, side: float = 1.5) -> list[Polygon]:
    shape = core.outline(plan)
    squares: list[Polygon] = []
    if shape is None:
        return squares
    for door in plan.doors:
        cx, cy = core.door_center(door.wall)
        half = side / 2.0
        options = [
            box(cx - half, cy - side, cx + half, cy),
            box(cx - half, cy, cx + half, cy + side),
            box(cx - side, cy - half, cx, cy + half),
            box(cx, cy - half, cx + side, cy + half),
        ]
        squares.append(max(options, key=lambda c: c.intersection(shape).area))
    return squares


SETTLE_WALL_M = 0.3
SETTLE_ITEM_M = 0.2


def _blocker_boxes(geometries) -> np.ndarray:
    """Габариты препятствий строками [min_x, min_y, max_x, max_y].

    Контур помещения раскладывается на рёбра: как один габарит он накрыл бы всё помещение.
    """
    rows: list[tuple[float, float, float, float]] = []
    for geometry in geometries:
        if isinstance(geometry, (LineString, LinearRing)):
            coords = list(geometry.coords)
            for (x1, y1), (x2, y2) in zip(coords, coords[1:]):
                rows.append((min(x1, x2), min(y1, y2), max(x1, x2), max(y1, y2)))
        else:
            rows.append(geometry.bounds)
    return np.array(rows, dtype=float).reshape(-1, 4)


def _settle(bounds: tuple[float, float, float, float], position: tuple[float, float], walls: np.ndarray, items: np.ndarray) -> tuple[float, float]:
    """Прижимает габарит к ближайшей стене или соседу по каждой оси, если зазор мал.

    Препятствие учитывается, если лежит напротив грани габарита (перекрывается с ней по
    поперечной оси) и отстоит от неё не дальше порога: стены — SETTLE_WALL_M, соседи — SETTLE_ITEM_M.
    """
    min_x, min_y, max_x, max_y = bounds
    x, y = position
    inset = 0.01
    shifts: dict[str, list[float]] = {"x": [], "y": []}
    for boxes, reach in ((walls, SETTLE_WALL_M), (items, SETTLE_ITEM_M)):
        if boxes.size == 0:
            continue
        bx0, by0, bx1, by1 = boxes[:, 0], boxes[:, 1], boxes[:, 2], boxes[:, 3]
        facing_x = (by0 < max_y - inset) & (by1 > min_y + inset)
        facing_y = (bx0 < max_x - inset) & (bx1 > min_x + inset)
        gaps = (
            ("x", 1, facing_x, bx0 - max_x),
            ("x", -1, facing_x, min_x - bx1),
            ("y", 1, facing_y, by0 - max_y),
            ("y", -1, facing_y, min_y - by1),
        )
        for axis, sign, facing, gap in gaps:
            near = facing & (gap > 1e-4) & (gap <= reach)
            if near.any():
                shifts[axis].append(sign * float(gap[near].min()))
    if shifts["x"]:
        x += min(shifts["x"], key=abs)
    if shifts["y"]:
        y += min(shifts["y"], key=abs)
    return round(x, 3), round(y, 3)


def _axes(rotation_deg: float, size: tuple[float, float]) -> tuple[tuple[float, float], tuple[float, float]]:
    """Направления длинной и короткой осей объекта после поворота."""
    angle = math.radians(-rotation_deg)
    x_axis = (math.cos(angle), math.sin(angle))
    y_axis = (-math.sin(angle), math.cos(angle))
    return (x_axis, y_axis) if size[0] >= size[1] else (y_axis, x_axis)


def _wall_alignment(poly: Polygon, rotation: float, size: tuple[float, float], walls: list[LineString]) -> tuple[float, float]:
    """Расстояние до ближайшей стены и перекос: длинная сторона должна идти вдоль стены."""
    center = poly.centroid
    nearest_wall = min(walls, key=lambda w: w.distance(poly))
    distance = nearest_wall.distance(poly)
    (x1, y1), (x2, y2) = nearest_wall.coords[0], nearest_wall.coords[-1]
    length = math.hypot(x2 - x1, y2 - y1) or 1.0
    wall_dir = ((x2 - x1) / length, (y2 - y1) / length)
    long_axis, _ = _axes(rotation, size)
    skew = abs(long_axis[0] * wall_dir[1] - long_axis[1] * wall_dir[0])
    _ = center
    return distance, skew


def _operator_side(plan: Plan, poly: Polygon, rotation: float, size: tuple[float, float]) -> tuple[float, float] | None:
    """Рабочее место оператора — со стороны служебной зоны, по короткой оси окна."""
    back = core.zone_by_type(plan, "back_office", "staff_room")
    center = poly.centroid
    if back:
        target = nearest_points(unary_union([core.zone_polygon(z) for z in back]), center)[0]
    else:
        walls = _walls(plan)
        if not walls:
            return None
        target = nearest_points(min(walls, key=lambda w: w.distance(center)), center)[0]
    _, short_axis = _axes(rotation, size)
    dx, dy = target.x - center.x, target.y - center.y
    sign = 1.0 if dx * short_axis[0] + dy * short_axis[1] >= 0 else -1.0
    offset = min(size) / 2.0 + 0.4
    return (round(center.x + sign * short_axis[0] * offset, 3), round(center.y + sign * short_axis[1] * offset, 3))


FitnessFn = Callable[[Plan, Polygon, float, tuple[float, float]], float]


@dataclass
class _FeatureContext:
    """Данные плана, общие для всех кандидатов одного поиска."""

    plan: Plan
    type_code: str
    type_def: EquipmentType
    walls: list[LineString]
    partitions: list[Polygon]
    obstacles: list[Polygon]
    windows: list[Polygon]
    door_squares: list[Polygon]
    entrance: tuple[float, float] | None
    shape: Polygon | None
    region: Polygon
    occupied: Polygon | None
    size: tuple[float, float]
    # Поток посетителей: линии от входа к месту посетителя у каждого окна, стойки и терминала,
    # плюс типовые маршруты плана. По ним видно, какое место перекроет чей-то путь.
    routes: list[LineString] = field(default_factory=list)
    access_points: list[Point] = field(default_factory=list)
    zones_by_kind: dict[str, list[Polygon]] = field(default_factory=dict)


# Виды оборудования, которые модель различает отдельно: у каждого своя логика размещения.
TYPE_FLAGS = (
    "cashier_window",
    "pvz_counter",
    "storage_rack",
    "waiting_seat",
    "self_service_kiosk",
    "atm",
    "info_stand",
    "package_drop_box",
)
FREE_SPACE_RADIUS_M = 1.2
# Ближе этого к линии движения объект уже сужает путь посетителя.
ROUTE_BAND_M = 0.9
ZONE_KINDS = ("waiting", "queue_accumulator", "vestibule", "back_office", "staff_room")


def _flow_context(plan: Plan, entrance: tuple[float, float] | None, exclude: set[str]) -> tuple[list[LineString], list[Point]]:
    """Линии движения посетителей и точки, где они стоят у объектов обслуживания."""
    routes: list[LineString] = []
    points: list[Point] = []
    for item in plan.equipment:
        if item.id in exclude or item.type not in VISITOR_TARGET_TYPES:
            continue
        access = passability.client_access_point(plan, item)
        points.append(Point(access))
        if entrance is not None and (abs(access[0] - entrance[0]) + abs(access[1] - entrance[1])) > 1e-6:
            routes.append(LineString([entrance, access]))
    if plan.flow_hints is not None:
        for path in plan.flow_hints.typical_paths:
            if len(path.waypoints) >= 2:
                routes.append(LineString(path.waypoints))
    return routes, points


def _features(poly: Polygon, rotation: float, score: float, ctx: _FeatureContext) -> dict[str, float]:
    """Признаки позиции для обучаемого ускорителя.

    Всё, что здесь считается, должно быть дешевле полной проверки на порядок: иначе ускоритель
    перестаёт ускорять. Дорогие величины (ширина пути, зоны разворота) сюда не попадают —
    именно их модель и предсказывает по косвенным признакам.
    """
    centre = poly.centroid
    zone = core.zone_of_point(ctx.plan, (centre.x, centre.y))
    zone_object = next((z for z in ctx.plan.zones if z.id == zone), None) if zone else None
    zone_poly = core.zone_polygon(zone_object) if zone_object else None
    zone_types_map = zone_types(ctx.plan)
    free_circle = centre.buffer(FREE_SPACE_RADIUS_M)
    if ctx.shape is not None:
        free_circle = free_circle.intersection(ctx.shape)
    free_area = free_circle.area
    if ctx.occupied is not None and free_area > 0:
        free_area = free_circle.difference(ctx.occupied).area
    entrance_point = Point(ctx.entrance) if ctx.entrance else None
    window_axis = 10.0
    if entrance_point is not None and ctx.windows:
        # Расстояние до линии «вход — окно обслуживания»: объект на этой линии мешает проходу.
        window_axis = min(
            LineString([entrance_point, w.centroid]).distance(poly) for w in ctx.windows
        )
    features: dict[str, float] = {
        "fitness": score,
        "wall_distance": min((w.distance(poly) for w in ctx.walls), default=0.0),
        "entrance_distance": poly.distance(entrance_point) if entrance_point else 0.0,
        "nearest_obstacle": min((o.distance(poly) for o in ctx.obstacles), default=10.0),
        "rotation": rotation,
        "area": poly.area,
        "boundary_distance": ctx.shape.exterior.distance(poly) if ctx.shape is not None else 0.0,
        "door_distance": min((sq.distance(poly) for sq in ctx.door_squares), default=10.0),
        "width": ctx.size[0],
        "depth": ctx.size[1],
        "aspect": ctx.size[0] / ctx.size[1] if ctx.size[1] else 1.0,
        "partition_distance": min((p.distance(poly) for p in ctx.partitions), default=10.0),
        "window_distance": min((w.distance(poly) for w in ctx.windows), default=10.0),
        "entrance_axis_distance": window_axis,
        "free_around": free_area / (math.pi * FREE_SPACE_RADIUS_M**2),
        "zone_public": 1.0 if zone_object and zone_types_map.get(zone_object.type, None) and zone_types_map[zone_object.type].public else 0.0,
        "zone_area": zone_poly.area if zone_poly is not None else 0.0,
        "zone_free_ratio": (
            max(0.0, zone_poly.difference(ctx.occupied).area / zone_poly.area)
            if zone_poly is not None and zone_poly.area > 0 and ctx.occupied is not None
            else 1.0
        ),
        "plan_area": ctx.shape.area if ctx.shape is not None else 0.0,
        "plan_objects": float(len(ctx.plan.equipment)),
        "plan_density": (
            sum(o.area for o in ctx.obstacles) / ctx.shape.area if ctx.shape is not None and ctx.shape.area else 0.0
        ),
        "region_area": ctx.region.area,
        "floor_object": 1.0 if ctx.type_def.floor_object else 0.0,
        "operator_workplace": 1.0 if getattr(ctx.type_def, "operator_workplace", False) else 0.0,
    }
    for flag in TYPE_FLAGS:
        features[f"type_{flag}"] = 1.0 if ctx.type_code == flag else 0.0

    # Признаки потока посетителей.
    route_distances = [line.distance(poly) for line in ctx.routes]
    features["route_distance"] = min(route_distances, default=10.0)
    features["route_hits"] = float(sum(1 for d in route_distances if d < ROUTE_BAND_M))
    features["access_distance"] = min((point.distance(poly) for point in ctx.access_points), default=10.0)
    features["route_pinch"] = (
        min(features["nearest_obstacle"], 3.0) if features["route_distance"] < ROUTE_BAND_M else 3.0
    )
    scale = math.sqrt(ctx.shape.area) if ctx.shape is not None and ctx.shape.area > 0 else 1.0
    features["entrance_relative"] = features["entrance_distance"] / scale
    for kind in ZONE_KINDS:
        features[f"in_{kind}"] = 1.0 if any(z.contains(centre) for z in ctx.zones_by_kind.get(kind, [])) else 0.0
    return features


def _fitness_for(type_code: str, type_def: EquipmentType) -> FitnessFn:
    """Оценка уместности позиции: чем меньше, тем лучше."""

    def near_wall(plan: Plan, poly: Polygon, rot: float, size: tuple[float, float]) -> float:
        distance, skew = _wall_alignment(poly, rot, size, _walls(plan))
        return distance + 2.0 * skew

    def near_entrance(plan: Plan, poly: Polygon, rot: float, size: tuple[float, float]) -> float:
        entrance = passability.entrance_point(plan)
        if entrance is None:
            return near_wall(plan, poly, rot, size)
        return poly.distance(Point(entrance)) + 0.3 * near_wall(plan, poly, rot, size)

    def service_window(plan: Plan, poly: Polygon, rot: float, size: tuple[float, float]) -> float:
        back = core.zone_by_type(plan, "back_office", "staff_room")
        if not back:
            return near_wall(plan, poly, rot, size)
        region = unary_union([core.zone_polygon(z) for z in back])
        target = nearest_points(region, poly.centroid)[0]
        _, short_axis = _axes(rot, size)
        dx, dy = target.x - poly.centroid.x, target.y - poly.centroid.y
        norm = math.hypot(dx, dy) or 1.0
        skew = 1.0 - abs((dx * short_axis[0] + dy * short_axis[1]) / norm)
        return poly.distance(region) + 2.0 * skew

    def near_windows(plan: Plan, poly: Polygon, rot: float, size: tuple[float, float]) -> float:
        windows = core.equipment_by_type(plan, *SERVICE_WINDOW_TYPES)
        if not windows:
            return near_wall(plan, poly, rot, size)
        return min(core.equipment_polygon(w).distance(poly) for w in windows)

    def far_from_entrance_on_wall(plan: Plan, poly: Polygon, rot: float, size: tuple[float, float]) -> float:
        """Камера на стене напротив входа: вход и окна попадают в кадр."""
        entrance = passability.entrance_point(plan)
        wall_distance, _ = _wall_alignment(poly, rot, size, _walls(plan))
        view = -poly.distance(Point(entrance)) if entrance else 0.0
        return wall_distance * 5.0 + view * 0.2

    def visible_from_waiting(plan: Plan, poly: Polygon, rot: float, size: tuple[float, float]) -> float:
        """Табло на стене, ближайшей к зонам ожидания и накопителю очереди."""
        wall_distance, skew = _wall_alignment(poly, rot, size, _walls(plan))
        waiting = core.zone_by_type(plan, "waiting", "queue_accumulator")
        if not waiting:
            return wall_distance * 5.0 + skew
        region = unary_union([core.zone_polygon(z) for z in waiting])
        return wall_distance * 5.0 + 2.0 * skew + region.centroid.distance(poly)

    if type_code in SERVICE_WINDOW_TYPES:
        return service_window
    if type_code in ("self_service_kiosk", "package_drop_box"):
        return near_entrance
    if type_code in ("weighing_station", "payment_terminal_stand"):
        return near_windows
    if type_code == "camera":
        return far_from_entrance_on_wall
    if type_code in ("info_display", "media_panel"):
        return visible_from_waiting
    return near_wall


def generate(
    plan: Plan,
    type_code: str,
    size: tuple[float, float] | None = None,
    zone_id: str | None = None,
    exclude: set[str] | None = None,
    step: float = GRID_STEP,
    prefer_near: tuple[float, float] | None = None,
) -> tuple[list[Candidate], int]:
    """Кандидаты, прошедшие быстрый фильтр, и общее число сгенерированных позиций."""
    types = equipment_types(plan)
    type_def = types.get(type_code) or EquipmentType(
        code=type_code, label=type_code, group="", default_size_m=size or (1.0, 1.0)
    )
    size = size or type_def.default_size_m
    region = _allowed_region(plan, type_def, zone_id)
    if region is None or region.is_empty:
        return [], 0

    exclude = exclude or set()
    floor = [i for i in core.floor_equipment(plan) if i.id not in exclude]
    obstacles = [core.equipment_polygon(i) for i in floor]
    partitions = [
        LineString([w.from_, w.to]).buffer(0.05, cap_style="flat")
        for w in plan.walls
        if w.type != "outer"
    ]
    door_squares = _door_squares(plan)
    door_passages = [core.door_clear_area(d.wall) for d in plan.doors]
    windows = [core.equipment_polygon(i) for i in floor if i.type in SERVICE_WINDOW_TYPES]
    passage = rules_by_id()["passage_between_workplaces"].value or 1.0
    is_window = type_code in SERVICE_WINDOW_TYPES
    kiosk_limit = rules_by_id()["queue_terminal_near_entrance"].value or 3.0
    entrance = passability.entrance_point(plan)
    walls = _walls(plan)
    tree = STRtree(obstacles) if obstacles else None
    fitness = _fitness_for(type_code, type_def)
    shape = core.outline(plan)

    # Всё, что нужно признакам кандидата, считается один раз на план, а не на каждую позицию.
    context = _FeatureContext(
        plan=plan,
        type_code=type_code,
        type_def=type_def,
        walls=walls,
        partitions=partitions,
        obstacles=obstacles,
        windows=windows,
        door_squares=door_squares,
        entrance=entrance,
        shape=shape,
        region=region,
        occupied=unary_union(obstacles) if obstacles else None,
        size=size,
    )
    context.routes, context.access_points = _flow_context(plan, entrance, exclude)
    context.zones_by_kind = {
        kind: [core.zone_polygon(z) for z in plan.zones if z.type == kind] for kind in ZONE_KINDS
    }

    min_x, min_y, max_x, max_y = region.bounds
    generated = 0
    result: list[Candidate] = []
    prepared_region = region.buffer(1e-6)
    shapely.prepare(prepared_region)
    rotations = (0.0, 90.0) if size[0] != size[1] else (0.0,)
    xs = [min_x + step / 2 + i * step for i in range(max(int((max_x - min_x) / step), 1))]
    ys = [min_y + step / 2 + j * step for j in range(max(int((max_y - min_y) / step), 1))]

    # Грани, к которым прижимается кандидат: внутренняя сторона наружных стен, перегородки и
    # соседнее оборудование. Шаг сетки не совпадает со стенами, и без прижатия между объектом
    # и стеной остаются зазоры в несколько сантиметров, которыми нельзя воспользоваться.
    wall_boxes = _blocker_boxes(([shape.exterior] if shape is not None else []) + partitions)
    item_boxes = _blocker_boxes(obstacles if type_def.floor_object else [])
    seen: set[tuple[float, float, float]] = set()

    def accept(x: float, y: float, rotation: float) -> Candidate | None:
        probe = Equipment(id="__candidate__", type=type_code, position=(x, y), size_m=size, rotation_deg=rotation)
        poly = core.equipment_polygon(probe)
        if not prepared_region.contains(poly):
            return None
        if type_def.floor_object:
            if tree is not None:
                hits = tree.query(poly, predicate="intersects")
                if any(obstacles[h].intersection(poly).area > 1e-3 for h in hits):
                    return None
            if any(p.intersection(poly).area > 1e-4 for p in partitions):
                return None
            if any(sq.intersection(poly).area > 1e-3 for sq in door_squares):
                return None
            if any(dp.intersection(poly).area > 0.02 for dp in door_passages):
                return None
            if is_window and any(0 <= w.distance(poly) < passage for w in windows):
                return None
            if not is_window and any(0 < w.distance(poly) < passage for w in windows):
                return None
        else:
            wall_distance = min((w.distance(poly) for w in walls), default=0.0)
            if type_code != "fire_alarm" and wall_distance > WALL_MOUNT_TOLERANCE:
                return None
        if type_code == "self_service_kiosk" and entrance is not None:
            if poly.distance(Point(entrance)) > kiosk_limit:
                return None
        score = fitness(plan, poly, rotation, size)
        if prefer_near is not None:
            score += 0.5 * math.hypot(x - prefer_near[0], y - prefer_near[1])
        operator = _operator_side(plan, poly, rotation, size) if is_window else None
        # Стул оператора в проёме перекрывает дверь так же, как сам объект.
        if operator is not None and any(core.seat_in_door_swing(d.wall, d.width_m, operator) for d in plan.doors):
            return None
        return Candidate(
            position=(round(x, 3), round(y, 3)),
            rotation_deg=rotation,
            polygon=poly,
            fitness=score,
            operator_position=operator,
            features=_features(poly, rotation, score, context),
        )

    for rotation in rotations:
        for x0 in xs:
            for y0 in ys:
                generated += 1
                raw = Equipment(id="__candidate__", type=type_code, position=(x0, y0), size_m=size, rotation_deg=rotation)
                raw_poly = core.equipment_polygon(raw)
                if not prepared_region.contains(raw_poly):
                    continue
                settled = _settle(raw_poly.bounds, (x0, y0), wall_boxes, item_boxes)
                # Прижатое место предпочтительнее; если оно нарушает зазоры, остаётся место сетки.
                for x, y in (settled, (round(x0, 3), round(y0, 3))):
                    if (x, y, rotation) in seen:
                        break
                    candidate = accept(x, y, rotation)
                    if candidate is not None:
                        seen.add((x, y, rotation))
                        result.append(candidate)
                        break
    result.sort(key=lambda c: c.fitness)
    return result, generated


def _route_floor(plan: Plan) -> float | None:
    from app.rules.engine import VISITOR_TARGET_TYPES

    routes = passability.client_route_widths(plan, VISITOR_TARGET_TYPES)
    reachable = [r.width_m for r in routes if r.reachable]
    if not routes:
        return None
    return min(reachable) if len(reachable) == len(routes) else 0.0


def with_equipment(plan: Plan, item: Equipment, replace_id: str | None = None) -> Plan:
    document = plan.model_dump(by_alias=True, exclude_none=True)
    equipment = [e for e in document.get("equipment", []) if e["id"] != replace_id]
    equipment.append(item.model_dump(by_alias=True, exclude_none=True))
    document["equipment"] = equipment
    return Plan.model_validate(document)


def evaluate(plan: Plan, candidate_plan: Plan, candidate: Candidate, baseline_errors: int) -> Evaluated:
    """Полная оценка варианта: нормы плюс все прочие стороны качества расстановки."""
    report = validate(candidate_plan, explain=False)
    width, widest = passability.route_summary(candidate_plan, VISITOR_TARGET_TYPES)
    return Evaluated(
        candidate=candidate,
        plan=candidate_plan,
        errors=report.errors,
        warnings=report.warnings,
        min_route_width=width,
        accepted=report.errors <= baseline_errors,
        score=objective.score(
            candidate_plan,
            plan,
            errors=report.errors,
            warnings=report.warnings,
            entrance_width=widest,
            narrowest_route=width,
            with_queue=False,
        ),
    )


Ranker = Callable[[list[Candidate]], list[Candidate]]

# Насколько сильно каждая голова модели сдвигает порядок относительно оценки уместности.
MODEL_WEIGHT = 4.0
QUALITY_WEIGHT = 8.0


def _prioritise(candidates: list[Candidate], with_quality: bool = True) -> list[Candidate]:
    """Порядок полной проверки: оценка уместности, уточнённая головами модели.

    Первая голова отвечает, пройдёт ли позиция полную проверку, — её предсказание поднимает
    кандидата в списке. Вторая обучена на порядок мест для нового объекта: какое место в списке
    даст лучшую расстановку. При перестановке уже стоящего объекта она не включается — на этом
    сценарии она не училась, и сквозной замер показал, что там она мешает исправлять ошибки.
    Без файла весов порядок остаётся прежним.
    """
    ranked = accelerator.rank_pairs([c.features for c in candidates])
    if ranked is None:
        return candidates
    # Оценки второй головы нормируются внутри списка: важен порядок мест друг относительно
    # друга, а не абсолютная величина, которая у разных помещений разная.
    qualities = np.array([quality for _, quality in ranked], dtype=float)
    spread = float(qualities.std())
    normalised = (qualities - qualities.mean()) / spread if spread > 1e-9 else np.zeros(len(qualities))
    if not with_quality:
        normalised = np.zeros(len(candidates))
    paired = sorted(
        zip(candidates, (p for p, _ in ranked), normalised),
        key=lambda item: item[0].fitness - MODEL_WEIGHT * item[1] + QUALITY_WEIGHT * item[2],
    )
    return [candidate for candidate, _, _ in paired]


def place(
    plan: Plan,
    type_code: str,
    *,
    new_id: str,
    size: tuple[float, float] | None = None,
    zone_id: str | None = None,
    template: Equipment | None = None,
    top_k: int = DEFAULT_TOP_K,
    keep: int = 3,
    ranker: Ranker | None = None,
    prefer_near: tuple[float, float] | None = None,
) -> SearchResult:
    """Лучшие позиции для нового объекта или для перемещения существующего (template)."""
    started = time.perf_counter()
    exclude = {template.id} if template is not None else set()
    base_plan = plan
    if template is not None:
        document = plan.model_dump(by_alias=True, exclude_none=True)
        document["equipment"] = [e for e in document["equipment"] if e["id"] != template.id]
        base_plan = Plan.model_validate(document)
    baseline_errors = validate(plan, explain=False).errors

    candidates, generated = generate(
        base_plan, type_code, size or (template.size_m if template else None), zone_id, exclude, prefer_near=prefer_near
    )
    passed = len(candidates)
    ordered = ranker(candidates) if ranker is not None else _prioritise(candidates, with_quality=template is None)
    shortlist = ordered[:top_k]

    types = equipment_types(plan)
    type_def = types.get(type_code)
    evaluated: list[Evaluated] = []
    for candidate in shortlist:
        base_fields = template.model_dump(by_alias=True, exclude_none=True) if template else {
            "id": new_id,
            "type": type_code,
            "label": type_def.label if type_def else type_code,
        }
        if type_def and type_def.capacity_persons and template is None:
            base_fields["capacity_persons"] = type_def.capacity_persons
        base_fields.update(
            {
                "position": candidate.position,
                "rotation_deg": candidate.rotation_deg,
                "size_m": size or (template.size_m if template else (type_def.default_size_m if type_def else (1.0, 1.0))),
                "zone": core.zone_of_point(base_plan, candidate.position),
            }
        )
        if candidate.operator_position is not None:
            base_fields["operator_position"] = candidate.operator_position
        item = Equipment.model_validate(base_fields)
        candidate_plan = with_equipment(base_plan, item)
        evaluated.append(evaluate(plan, candidate_plan, candidate, baseline_errors))

    accepted = [e for e in evaluated if e.accepted]
    # Сначала меньше нарушений, при равенстве — лучшая расстановка целиком: проход, места
    # ожидания, путь посетителя и величина переноса.
    accepted.sort(
        key=lambda e: (
            e.errors,
            e.score.total if e.score is not None else 0.0,
            -(e.min_route_width if e.min_route_width is not None else 10.0),
            e.candidate.fitness,
        )
    )
    return SearchResult(
        equipment_type=type_code,
        generated=generated,
        passed_fast_filter=passed,
        fully_checked=len(evaluated),
        elapsed_ms=round((time.perf_counter() - started) * 1000, 1),
        baseline_errors=baseline_errors,
        best=_distinct(accepted, keep),
    )


def _distinct(items: list[Evaluated], keep: int, min_gap: float = 0.75) -> list[Evaluated]:
    """Варианты должны отличаться местом, а не сдвигом на одну клетку сетки."""
    chosen: list[Evaluated] = []
    for item in items:
        x, y = item.candidate.position
        if all(math.hypot(x - c.candidate.position[0], y - c.candidate.position[1]) >= min_gap for c in chosen):
            chosen.append(item)
        if len(chosen) == keep:
            break
    return chosen
