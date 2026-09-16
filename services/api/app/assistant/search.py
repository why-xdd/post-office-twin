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

import shapely
from shapely.geometry import LineString, Point, Polygon, box
from shapely.ops import nearest_points, unary_union
from shapely.strtree import STRtree

from app.dictionaries import EquipmentType, equipment_types, zone_types
from app.geometry import core, passability
from app.models import Equipment, Plan
from app.rules.catalog import rules_by_id
from app.rules.engine import SERVICE_WINDOW_TYPES, validate

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
    windows = [core.equipment_polygon(i) for i in floor if i.type in SERVICE_WINDOW_TYPES]
    passage = rules_by_id()["passage_between_workplaces"].value or 1.0
    is_window = type_code in SERVICE_WINDOW_TYPES
    kiosk_limit = rules_by_id()["queue_terminal_near_entrance"].value or 3.0
    entrance = passability.entrance_point(plan)
    walls = _walls(plan)
    tree = STRtree(obstacles) if obstacles else None
    fitness = _fitness_for(type_code, type_def)
    shape = core.outline(plan)

    min_x, min_y, max_x, max_y = region.bounds
    generated = 0
    result: list[Candidate] = []
    prepared_region = region.buffer(1e-6)
    shapely.prepare(prepared_region)
    rotations = (0.0, 90.0) if size[0] != size[1] else (0.0,)
    xs = [min_x + step / 2 + i * step for i in range(max(int((max_x - min_x) / step), 1))]
    ys = [min_y + step / 2 + j * step for j in range(max(int((max_y - min_y) / step), 1))]

    for rotation in rotations:
        for x in xs:
            for y in ys:
                generated += 1
                probe = Equipment(id="__candidate__", type=type_code, position=(x, y), size_m=size, rotation_deg=rotation)
                poly = core.equipment_polygon(probe)
                if not prepared_region.contains(poly):
                    continue
                if type_def.floor_object:
                    if tree is not None:
                        hits = tree.query(poly, predicate="intersects")
                        if any(obstacles[h].intersection(poly).area > 1e-3 for h in hits):
                            continue
                    if any(p.intersects(poly) for p in partitions):
                        continue
                    if any(sq.intersection(poly).area > 1e-3 for sq in door_squares):
                        continue
                    if is_window and any(0 <= w.distance(poly) < passage for w in windows):
                        continue
                    if not is_window and any(0 < w.distance(poly) < passage for w in windows):
                        continue
                else:
                    wall_distance = min((w.distance(poly) for w in walls), default=0.0)
                    if type_code != "fire_alarm" and wall_distance > WALL_MOUNT_TOLERANCE:
                        continue
                if type_code == "self_service_kiosk" and entrance is not None:
                    if poly.distance(Point(entrance)) > kiosk_limit:
                        continue
                score = fitness(plan, poly, rotation, size)
                if prefer_near is not None:
                    score += 0.5 * math.hypot(x - prefer_near[0], y - prefer_near[1])
                operator = _operator_side(plan, poly, rotation, size) if is_window else None
                wall_distance = min((w.distance(poly) for w in walls), default=0.0)
                result.append(
                    Candidate(
                        position=(round(x, 3), round(y, 3)),
                        rotation_deg=rotation,
                        polygon=poly,
                        fitness=score,
                        operator_position=operator,
                        features={
                            "fitness": score,
                            "wall_distance": wall_distance,
                            "entrance_distance": poly.distance(Point(entrance)) if entrance else 0.0,
                            "nearest_obstacle": min((o.distance(poly) for o in obstacles), default=10.0),
                            "rotation": rotation,
                            "area": poly.area,
                            "boundary_distance": shape.exterior.distance(poly) if shape is not None else 0.0,
                            "door_distance": min((sq.distance(poly) for sq in door_squares), default=10.0),
                        },
                    )
                )
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
    report = validate(candidate_plan, explain=False)
    width = _route_floor(candidate_plan)
    return Evaluated(
        candidate=candidate,
        plan=candidate_plan,
        errors=report.errors,
        warnings=report.warnings,
        min_route_width=width,
        accepted=report.errors <= baseline_errors,
    )


Ranker = Callable[[list[Candidate]], list[Candidate]]


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
    ordered = ranker(candidates) if ranker is not None else candidates
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
    accepted.sort(
        key=lambda e: (
            e.errors,
            -(e.min_route_width if e.min_route_width is not None else 10.0),
            e.warnings,
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
