"""Проходимость помещения.

Ширина пути проверяется через пространство конфигураций: свободная площадь помещения сужается
на половину требуемой ширины. Если после сужения две точки остаются в одной связной области,
между ними существует путь не уже заданной ширины. Бинарный поиск по ширине даёт фактическую
ширину самого узкого места маршрута.
"""

from __future__ import annotations

from dataclasses import dataclass

from shapely.geometry import MultiPolygon, Point, Polygon
from shapely.ops import nearest_points, unary_union

from app.geometry import core
from app.models import Equipment, Plan

# Точность бинарного поиска ширины, метры.
WIDTH_TOLERANCE = 0.01
# Верхняя граница поиска: шире этого проходы в отделении не бывают.
MAX_SEARCH_WIDTH = 6.0


@dataclass(frozen=True)
class RouteWidth:
    target_id: str
    width_m: float
    reachable: bool


def free_space(plan: Plan, exclude: set[str] | None = None) -> Polygon | MultiPolygon | None:
    """Площадь пола, свободная от оборудования. Внутренние перегородки считаются препятствием."""
    shape = core.outline(plan)
    if shape is None:
        return None
    exclude = exclude or set()
    obstacles = [
        core.equipment_polygon(item) for item in plan.equipment if item.id not in exclude
    ]
    obstacles.extend(
        _wall_obstacle(plan, wall.id)
        for wall in plan.walls
        if wall.type != "outer"
    )
    obstacles = [o for o in obstacles if o is not None and not o.is_empty]
    if not obstacles:
        return shape
    return shape.difference(unary_union(obstacles))


def _wall_obstacle(plan: Plan, wall_id: str) -> Polygon | None:
    """Перегородка как полоса толщиной 10 см с вырезанными дверными проёмами."""
    wall = next(w for w in plan.walls if w.id == wall_id)
    from shapely.geometry import LineString

    band = LineString([wall.from_, wall.to]).buffer(0.05, cap_style="flat")
    for door in plan.doors:
        x1, y1, x2, y2 = door.wall
        opening = LineString([(x1, y1), (x2, y2)])
        if opening.distance(LineString([wall.from_, wall.to])) < 0.06:
            band = band.difference(opening.buffer(0.2, cap_style="flat"))
    return band


def _component_containing(space: Polygon | MultiPolygon, probe: Point) -> Polygon | None:
    parts = list(space.geoms) if isinstance(space, MultiPolygon) else [space]
    for part in parts:
        if part.buffer(1e-6).contains(probe):
            return part
    return None


def _reach(space: Polygon | MultiPolygon, point: Point, radius: float) -> Point | None:
    """Позиция центра человека, чьё тело радиуса radius накрывает точку.

    Если допустимая область дальше radius, человек этой ширины до точки не доходит. Без этого
    ограничения узкое место у самой цели маскировалось бы притяжением к свободной области.
    """
    if space.contains(point):
        return point
    nearest = nearest_points(space, point)[0]
    if nearest.distance(point) > radius + 1e-6:
        return None
    return nearest


def _connected(space: Polygon | MultiPolygon, a: Point, b: Point, radius: float) -> bool:
    if space.is_empty:
        return False
    first_pos, second_pos = _reach(space, a, radius), _reach(space, b, radius)
    if first_pos is None or second_pos is None:
        return False
    first = _component_containing(space, first_pos)
    second = _component_containing(space, second_pos)
    return first is not None and second is not None and first.equals(second)


def _eroded(space: Polygon | MultiPolygon, width: float) -> Polygon | MultiPolygon:
    return space.buffer(-width / 2.0, join_style="mitre")


def route_width(
    plan: Plan, start: tuple[float, float], goal: tuple[float, float], exclude: set[str]
) -> tuple[float, bool]:
    """Ширина самого узкого места на лучшем пути между двумя точками.

    Возвращает (ширина, достижима ли цель вообще). Точки старта и цели считаются стоящими
    у объектов: сами объекты-цели исключаются из препятствий.
    """
    space = free_space(plan, exclude)
    if space is None or space.is_empty:
        return 0.0, False
    start_pt, goal_pt = Point(start), Point(goal)
    tiny = _eroded(space, 0.02)
    if not _connected(tiny, start_pt, goal_pt, 0.01):
        return 0.0, False

    low, high = 0.02, MAX_SEARCH_WIDTH
    while high - low > WIDTH_TOLERANCE:
        mid = (low + high) / 2.0
        eroded = _eroded(space, mid)
        if not eroded.is_empty and _connected(eroded, start_pt, goal_pt, mid / 2.0):
            low = mid
        else:
            high = mid
    return round(low, 2), True


def entrance_point(plan: Plan) -> tuple[float, float] | None:
    preferred = plan.flow_hints.main_entrance if plan.flow_hints else None
    door = next((d for d in plan.doors if preferred and d.id == preferred), None)
    door = door or next((d for d in plan.doors if d.type == "entrance"), None)
    if door is None:
        return None
    shape = core.outline(plan)
    center = Point(core.door_center(door.wall))
    if shape is None:
        return (center.x, center.y)
    # Точка входа сдвигается на 30 см внутрь помещения.
    inner = nearest_points(shape.buffer(-0.3), center)[0]
    return (inner.x, inner.y)


def client_access_point(plan: Plan, item: Equipment) -> tuple[float, float]:
    """Точка, в которой стоит посетитель у объекта.

    Для окна обслуживания это сторона, противоположная рабочему месту оператора. Для
    остальных объектов — ближайшая к входу сторона габарита.
    """
    poly = core.equipment_polygon(item)
    cx, cy = item.position
    if item.operator_position is not None:
        ox, oy = item.operator_position
        dx, dy = cx - ox, cy - oy
        length = max((dx * dx + dy * dy) ** 0.5, 1e-9)
        reach = max(item.size_m) / 2.0 + 0.4
        return (cx + dx / length * reach, cy + dy / length * reach)
    entrance = entrance_point(plan)
    if entrance is None:
        return (cx, cy)
    edge = nearest_points(poly.exterior, Point(entrance))[0]
    dx, dy = edge.x - cx, edge.y - cy
    length = max((dx * dx + dy * dy) ** 0.5, 1e-9)
    return (edge.x + dx / length * 0.4, edge.y + dy / length * 0.4)


def client_route_widths(plan: Plan, target_types: tuple[str, ...]) -> list[RouteWidth]:
    """Фактическая ширина пути от входа до каждого объекта обслуживания посетителей."""
    start = entrance_point(plan)
    if start is None:
        return []
    results: list[RouteWidth] = []
    for item in core.equipment_by_type(plan, *target_types):
        goal = client_access_point(plan, item)
        width, reachable = route_width(plan, start, goal, exclude={item.id})
        results.append(RouteWidth(target_id=item.id, width_m=width, reachable=reachable))
    return results


def largest_clear_circle(plan: Plan, zone_types: tuple[str, ...]) -> tuple[float, tuple[float, float] | None]:
    """Диаметр наибольшего свободного круга в клиентских зонах и его центр.

    Нужен для проверки зоны разворота кресла-коляски.
    """
    space = free_space(plan)
    zones = core.zone_by_type(plan, *zone_types)
    if space is None or not zones:
        return 0.0, None
    area = space.intersection(unary_union([core.zone_polygon(z) for z in zones]))
    if area.is_empty:
        return 0.0, None
    low, high = 0.0, MAX_SEARCH_WIDTH
    center: tuple[float, float] | None = None
    while high - low > WIDTH_TOLERANCE:
        mid = (low + high) / 2.0
        eroded = area.buffer(-mid / 2.0)
        if eroded.is_empty:
            high = mid
        else:
            low = mid
            point = eroded.representative_point()
            center = (round(point.x, 2), round(point.y, 2))
    return round(low, 2), center
