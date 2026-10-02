"""Пути эвакуации и огнетушители.

Ширина путей эвакуации — СП 1.13130.2020 п. 4.3.3: не менее 1,2 м, если по пути могут
эвакуироваться больше 50 человек; 0,7 м — для проходов к одиночным рабочим местам; 1,0 м —
во всех остальных случаях. Правила противопожарного режима (ПП РФ № 1479) разрешают ставить на
путях эвакуации места для ожидания и оборудование (п. 27 «б»), но требуют сохранить эту ширину
(п. 28) — поэтому ширина считается с учётом всей мебели и оборудования.

Как считается: свободный пол сужается на половину нормы с каждой стороны. Если часть зоны после
этого не связана ни с одним выходом наружу, то оттуда нет пути нужной ширины — это нарушение,
и сервис меряет, какой ширины путь там всё-таки есть.

Огнетушители — ПП РФ № 1479: в общественном здании на этаже не менее двух (п. 400), путь от
любого места до огнетушителя не длиннее 20 м с учётом перегородок и оборудования (п. 406).
"""

from __future__ import annotations

import heapq
import math
from dataclasses import dataclass, field

import numpy as np
import shapely
from shapely.geometry import MultiPolygon, Point, Polygon
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

EXTINGUISHER_TYPE = "fire_extinguisher"
EXTINGUISHER_MIN_COUNT = 2
EXTINGUISHER_REACH_M = 20.0
GRID_M = 0.25


@dataclass
class Bottleneck:
    zone_id: str
    zone_label: str
    required_m: float
    measured_m: float
    culprits: list[str] = field(default_factory=list)
    point: tuple[float, float] | None = None


def exits(plan: Plan) -> list[tuple[float, float]]:
    """Выходы наружу — главный и служебный вход, точка в 30 см внутри помещения."""
    shape = core.outline(plan)
    points = []
    for door in plan.doors:
        if door.type not in EXIT_DOORS:
            continue
        center = Point(core.door_center(door.wall))
        if shape is not None and not shape.buffer(-0.3).is_empty:
            inner = nearest_points(shape.buffer(-0.3), center)[0]
            points.append((inner.x, inner.y))
        else:
            points.append((center.x, center.y))
    return points


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


def bottlenecks(plan: Plan, space: Polygon | MultiPolygon | None = None) -> list[Bottleneck]:
    """Части зон, откуда к выходу нет пути нормативной ширины. space — свободный пол, если он
    отличается от плана (например, с людьми очереди)."""
    exit_points = exits(plan)
    if not exit_points or not plan.zones:
        return []
    if space is None:
        cache = passability.erosion(plan)
    else:
        cache = passability.ErosionCache(space) if not space.is_empty else None
    if cache is None:
        return []
    floor = core.floor_equipment(plan)
    found: list[Bottleneck] = []
    reported: set[tuple[int, int]] = set()
    for zone in plan.zones:
        need = required_width(plan, zone.type)
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
        shape = core.zone_polygon(zone)
        for index, part in enumerate(region.parts):
            if index in reachable or (width_cm, index) in reported:
                continue
            inside = part.intersection(shape)
            if inside.area < MIN_PART_SQM:
                continue
            reported.add((width_cm, index))
            probe = inside.representative_point()
            measured = max(cache.width(probe, Point(e))[0] for e in exit_points)
            exit_parts = [region.parts[i] for i in reachable]
            culprits = sorted(
                (
                    (item, core.equipment_polygon(item))
                    for item in floor
                ),
                key=lambda pair: pair[1].distance(part),
            )
            names = [
                item.id
                for item, poly in culprits
                if poly.distance(part) < need * 0.75
                and (not exit_parts or min(poly.distance(p) for p in exit_parts) < need * 0.75)
            ][:4]
            found.append(
                Bottleneck(
                    zone_id=zone.id,
                    zone_label=zone.label or zone.type,
                    required_m=need,
                    measured_m=measured,
                    culprits=names,
                    point=(round(probe.x, 2), round(probe.y, 2)),
                )
            )
    return found


def extinguisher_reach(plan: Plan) -> tuple[float, tuple[float, float]] | None:
    """Самый дальний путь до ближайшего огнетушителя и точка, откуда он начинается.

    Путь ищется по сетке 25 см по свободному полу: в обход перегородок и оборудования, как
    требует п. 406. None — огнетушителей нет или считать не по чему.
    """
    extinguishers = [e for e in plan.equipment if e.type == EXTINGUISHER_TYPE]
    space = passability.free_space(plan)
    if not extinguishers or space is None or space.is_empty:
        return None
    min_x, min_y, max_x, max_y = space.bounds
    xs = np.arange(min_x + GRID_M / 2, max_x, GRID_M)
    ys = np.arange(min_y + GRID_M / 2, max_y, GRID_M)
    gx, gy = np.meshgrid(xs, ys)
    inside = shapely.contains_xy(space, gx, gy)
    rows, cols = inside.shape
    distance = np.full(inside.shape, np.inf)
    queue: list[tuple[float, int, int]] = []
    for item in extinguishers:
        # Огнетушитель висит на стене: старт — ближайшая к нему клетка свободного пола.
        ex, ey = item.position
        d2 = (gx - ex) ** 2 + (gy - ey) ** 2
        d2[~inside] = np.inf
        r, c = np.unravel_index(int(np.argmin(d2)), d2.shape)
        if not math.isfinite(d2[r, c]):
            continue
        start = math.sqrt(float(d2[r, c]))
        if start < distance[r, c]:
            distance[r, c] = start
            heapq.heappush(queue, (start, r, c))
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
    reachable = np.where(np.isfinite(distance) & inside, distance, -1.0)
    if reachable.max() < 0:
        return None
    r, c = np.unravel_index(int(np.argmax(reachable)), reachable.shape)
    return round(float(reachable[r, c]), 1), (round(float(gx[r, c]), 2), round(float(gy[r, c]), 2))
