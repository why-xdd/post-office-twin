"""Пути эвакуации и огнетушители.

Ширина путей эвакуации — СП 1.13130.2020 п. 4.3.3: не менее 1,2 м, если по пути могут
эвакуироваться больше 50 человек; 0,7 м — для проходов к одиночным рабочим местам; 1,0 м —
во всех остальных случаях. Правила противопожарного режима (ПП РФ № 1479) разрешают ставить на
путях эвакуации места для ожидания и оборудование (п. 27 «б»), но требуют сохранить эту ширину
(п. 28) — поэтому ширина считается с учётом всей мебели и оборудования.

Как считается: свободный пол сужается на половину нормы с каждой стороны. Если часть зоны после
этого не связана ни с одним выходом наружу, то оттуда нет пути нужной ширины — это нарушение,
и сервис меряет, какой ширины путь там всё-таки есть и где именно он самый узкий.

Огнетушители — ПП РФ № 1479: в общественном здании на этаже не менее двух (п. 400), путь от
любого места до огнетушителя не длиннее 20 м с учётом перегородок и оборудования (п. 406).
"""

from __future__ import annotations

import heapq
import math
from dataclasses import dataclass, field

import numpy as np
import shapely
from shapely.geometry import LineString, MultiPolygon, Point, Polygon
from shapely.ops import nearest_points

from app.geometry import core, passability
from app.models import Plan

WIDTH_MANY_M = 1.2
WIDTH_DEFAULT_M = 1.0
WIDTH_SINGLE_WORKPLACE_M = 0.7
MANY_PEOPLE = 50
PUBLIC_ZONES = ("client_area", "waiting", "queue_accumulator", "vestibule")
STAFF_ZONES = ("back_office", "staff_room")
EXIT_DOORS = ("entrance", "service")
# Часть зоны меньше этой площади после сужения — закуток, а не место, откуда эвакуируются люди.
MIN_PART_SQM = 0.3
STANDING_SQM = 0.5
# Без зон на плане эвакуация проверяется по всему помещению как по одной зоне.
WHOLE_ROOM = "Помещение"

EXTINGUISHER_TYPE = "fire_extinguisher"
EXTINGUISHER_MIN_COUNT = 2
EXTINGUISHER_REACH_M = 20.0
GRID_M = 0.25
# Центр человека держится от препятствий не ближе этого. Шаг сетки по диагонали (0,35 м) короче
# двух таких отступов и толщины перегородки (0,4 м), поэтому путь не проходит сквозь стену.
BODY_RADIUS_M = 0.15
# Огнетушитель висит на стене: до первой клетки пола от него не дальше этого и без стены между.
EXTINGUISHER_SEED_M = 1.0
# Клеток без пути к огнетушителю должно набраться хотя бы на столько, чтобы это было место, а не щель.
UNREACHABLE_MIN_SQM = 1.0


@dataclass
class Bottleneck:
    zone_id: str
    zone_label: str
    required_m: float
    measured_m: float
    culprits: list[str] = field(default_factory=list)
    point: tuple[float, float] | None = None
    gap: tuple[tuple[float, float], tuple[float, float]] | None = None
    exit_label: str | None = None


@dataclass
class ExtinguisherReach:
    """Как далеко до ближайшего огнетушителя и есть ли места, откуда до него нет пути."""

    farthest_m: float | None
    farthest_point: tuple[float, float] | None
    unreachable_sqm: float = 0.0
    unreachable_point: tuple[float, float] | None = None


EXIT_LABELS = {"entrance": "главный вход", "service": "служебный выход"}


def exit_doors(plan: Plan) -> list[tuple[tuple[float, float], str]]:
    """Выходы наружу с подписью: точка в 30 см внутри помещения и что это за дверь."""
    shape = core.outline(plan)
    inner_shape = shape.buffer(-0.3) if shape is not None else None
    result = []
    for door in plan.doors:
        if door.type not in EXIT_DOORS:
            continue
        center = Point(core.door_center(door.wall))
        if inner_shape is not None and not inner_shape.is_empty:
            inner = nearest_points(inner_shape, center)[0]
            point = (inner.x, inner.y)
        else:
            point = (center.x, center.y)
        result.append((point, EXIT_LABELS[door.type]))
    return result


def exits(plan: Plan) -> list[tuple[float, float]]:
    """Выходы наружу — главный и служебный вход, точка в 30 см внутри помещения."""
    return [point for point, _label in exit_doors(plan)]


def occupancy(plan: Plan) -> int:
    """Сколько человек может быть в отделении: места ожидания, стоящие в накопителе, сотрудники."""
    seats = sum(item.capacity_persons or 0 for item in plan.equipment)
    standing = sum(
        core.zone_polygon(zone).area / STANDING_SQM for zone in core.zone_by_type(plan, "queue_accumulator")
    )
    staff = sum(1 for item in plan.equipment if item.operator_position is not None or item.type == "back_office_workplace")
    return int(seats + standing + staff)


def required_width(plan: Plan, zone_type: str) -> float:
    if zone_type in STAFF_ZONES:
        return WIDTH_SINGLE_WORKPLACE_M
    return WIDTH_MANY_M if occupancy(plan) > MANY_PEOPLE else WIDTH_DEFAULT_M


def _zones(plan: Plan) -> list[tuple[str, str, str, Polygon]]:
    """Зоны плана, а без них — всё помещение одной клиентской зоной."""
    if plan.zones:
        return [(z.id, z.label or z.type, z.type, core.zone_polygon(z)) for z in plan.zones]
    shape = core.outline(plan)
    return [] if shape is None else [("", WHOLE_ROOM, "client_area", shape)]


def bottlenecks(
    plan: Plan, space: Polygon | MultiPolygon | None = None, explain: bool = True
) -> list[Bottleneck]:
    """Части зон, откуда к выходу нет пути нормативной ширины.

    space — свободный пол, если он отличается от плана (например, с людьми очереди). explain —
    искать ли самое узкое место и объекты, которые его создают: это нужно человеку, а не счёту
    нарушений при переборе мест в ассистенте.
    """
    doors = exit_doors(plan)
    exit_points = [point for point, _label in doors]
    labels = dict(doors)
    zones = _zones(plan)
    if not exit_points or not zones:
        return []
    if space is None:
        cache = passability.erosion(plan)
    else:
        cache = passability.ErosionCache(space) if not space.is_empty else None
    if cache is None:
        return []
    found: list[Bottleneck] = []
    reported: set[tuple[int, int]] = set()
    for zone_id, zone_label, zone_type, shape in zones:
        need = required_width(plan, zone_type)
        width_cm = int(round(need * 100))
        region = cache.at(width_cm)
        if region.is_empty:
            continue
        reachable = set()
        for point in exit_points:
            reached = region.reach(Point(point), need / 2 + 0.3)
            if reached is not None:
                index = region.component(reached)
                if index is not None:
                    reachable.add(index)
        for index, part in enumerate(region.parts):
            if index in reachable or (width_cm, index) in reported:
                continue
            inside = part.intersection(shape)
            if inside.area < MIN_PART_SQM:
                continue
            reported.add((width_cm, index))
            probe = inside.representative_point()
            widths = [(cache.width(probe, Point(e))[0], e) for e in exit_points]
            measured, best_exit = max(widths)
            neck = Bottleneck(
                zone_id=zone_id,
                zone_label=zone_label,
                required_m=need,
                measured_m=measured,
                point=(round(probe.x, 2), round(probe.y, 2)),
                # Выход называется, только когда их несколько: иначе и так ясно, о каком речь.
                exit_label=labels.get(best_exit) if len(exit_points) > 1 else None,
            )
            # Место и виновники — по точной геометрии: объекты на концах самого узкого просвета.
            # Стены и перегородки виновниками не бывают — их не переставишь.
            if explain and space is None and measured > 0:
                gap = passability.pinch(plan, cache, probe, Point(best_exit), measured)
                if gap is not None:
                    neck.gap = (gap.a, gap.b)
                    neck.culprits = list(gap.objects)
            found.append(neck)
    return found


def extinguisher_reach(plan: Plan) -> ExtinguisherReach | None:
    """Самый дальний путь до ближайшего огнетушителя и места, откуда до него пути нет вовсе.

    Путь ищется по сетке 25 см там, где может стоять человек: центр не ближе 15 см к препятствию.
    Так шаг сетки не перескакивает перегородку, а путь обходит стены и оборудование, как требует
    п. 406. None — огнетушителей нет или считать не по чему.
    """
    extinguishers = [e for e in plan.equipment if e.type == EXTINGUISHER_TYPE]
    space = passability.free_space(plan)
    if not extinguishers or space is None or space.is_empty:
        return None
    walkable = space.buffer(-BODY_RADIUS_M)
    if walkable.is_empty:
        return None
    min_x, min_y, max_x, max_y = walkable.bounds
    xs = np.arange(min_x + GRID_M / 2, max_x, GRID_M)
    ys = np.arange(min_y + GRID_M / 2, max_y, GRID_M)
    if len(xs) == 0 or len(ys) == 0:
        return None
    gx, gy = np.meshgrid(xs, ys)
    inside = shapely.contains_xy(walkable, gx, gy)
    if not inside.any():
        return None
    rows, cols = inside.shape
    distance = np.full(inside.shape, np.inf)
    queue: list[tuple[float, int, int]] = []
    room = space.buffer(0.02)
    for item in extinguishers:
        # Старт — ближайшая клетка, до которой от огнетушителя можно дотянуться, не проходя сквозь стену.
        ex, ey = item.position
        d2 = (gx - ex) ** 2 + (gy - ey) ** 2
        d2[~inside] = np.inf
        d2[d2 > EXTINGUISHER_SEED_M**2] = np.inf
        for flat in np.argsort(d2, axis=None)[:12]:
            r, c = np.unravel_index(int(flat), d2.shape)
            if not math.isfinite(d2[r, c]):
                break
            if room.covers(LineString([(ex, ey), (float(gx[r, c]), float(gy[r, c]))])):
                start = math.sqrt(float(d2[r, c]))
                if start < distance[r, c]:
                    distance[r, c] = start
                    heapq.heappush(queue, (start, r, c))
                break
    steps = [(-1, 0, GRID_M), (1, 0, GRID_M), (0, -1, GRID_M), (0, 1, GRID_M)] + [
        (dr, dc, GRID_M * math.sqrt(2)) for dr in (-1, 1) for dc in (-1, 1)
    ]
    while queue:
        dist, r, c = heapq.heappop(queue)
        if dist > distance[r, c]:
            continue
        for dr, dc, cost in steps:
            nr, nc = r + dr, c + dc
            if 0 <= nr < rows and 0 <= nc < cols and inside[nr, nc]:
                candidate = dist + cost
                if candidate < distance[nr, nc]:
                    distance[nr, nc] = candidate
                    heapq.heappush(queue, (candidate, nr, nc))
    reached = np.isfinite(distance) & inside
    lost = inside & ~np.isfinite(distance)
    result = ExtinguisherReach(farthest_m=None, farthest_point=None)
    if reached.any():
        values = np.where(reached, distance, -1.0)
        r, c = np.unravel_index(int(np.argmax(values)), values.shape)
        result.farthest_m = round(float(values[r, c]), 1)
        result.farthest_point = (round(float(gx[r, c]), 2), round(float(gy[r, c]), 2))
    # Без пути считаются только связные куски от квадратного метра: узкие щели между мебелью
    # тоже отрезаны, но это не место, где окажется человек.
    pockets = [p for p in _pockets(lost) if len(p) * GRID_M * GRID_M >= UNREACHABLE_MIN_SQM]
    if pockets:
        largest = max(pockets, key=len)
        r, c = largest[len(largest) // 2]
        result.unreachable_sqm = round(sum(len(p) for p in pockets) * GRID_M * GRID_M, 1)
        result.unreachable_point = (round(float(gx[r, c]), 2), round(float(gy[r, c]), 2))
    return result


def _pockets(mask: np.ndarray) -> list[list[tuple[int, int]]]:
    """Связные куски клеток маски (соседство по стороне и по углу), каждый — список клеток."""
    rows, cols = mask.shape
    seen = np.zeros(mask.shape, dtype=bool)
    pockets: list[list[tuple[int, int]]] = []
    for r0, c0 in np.argwhere(mask):
        if seen[r0, c0]:
            continue
        seen[r0, c0] = True
        stack = [(int(r0), int(c0))]
        cells: list[tuple[int, int]] = []
        while stack:
            r, c = stack.pop()
            cells.append((r, c))
            for dr in (-1, 0, 1):
                for dc in (-1, 0, 1):
                    nr, nc = r + dr, c + dc
                    if 0 <= nr < rows and 0 <= nc < cols and mask[nr, nc] and not seen[nr, nc]:
                        seen[nr, nc] = True
                        stack.append((nr, nc))
        pockets.append(cells)
    return pockets
