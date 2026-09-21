"""Проходимость помещения.

Ширина пути проверяется через пространство конфигураций: свободная площадь помещения сужается
на половину требуемой ширины. Если после сужения две точки остаются в одной связной области,
между ними существует путь не уже заданной ширины. Бинарный поиск по ширине даёт фактическую
ширину самого узкого места маршрута.
"""

from __future__ import annotations

from dataclasses import dataclass

from shapely.geometry import LineString, MultiPolygon, Point, Polygon
from shapely.ops import nearest_points, unary_union

from app.geometry import core
from app.models import Equipment, Plan

# Точность бинарного поиска ширины, метры.
WIDTH_TOLERANCE = 0.01
# Верхняя граница поиска: шире этого проходы в отделении не бывают.
MAX_SEARCH_WIDTH = 6.0
# Для маршрутов ширина выше этой границы нормативно неразличима (максимальная норма — 1,8 м),
# поэтому поиск останавливается на ней: результат «не уже 3,2 м».
ROUTE_WIDTH_CAP_CM = 320


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
        core.equipment_polygon(item)
        for item in core.floor_equipment(plan)
        if item.id not in exclude
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


def _component_index(space: Polygon | MultiPolygon, probe: Point) -> int | None:
    """Номер связной части свободной области, в которой лежит точка.

    Сравнивать части по номеру, а не по геометрии: равенство многоугольников на плане в сотни
    объектов стоит дороже всего остального расчёта маршрута.
    """
    parts = list(space.geoms) if isinstance(space, MultiPolygon) else [space]
    for index, part in enumerate(parts):
        if part.distance(probe) < 1e-6:
            return index
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
    first = _component_index(space, first_pos)
    return first is not None and first == _component_index(space, second_pos)


def _eroded(space: Polygon | MultiPolygon, width: float) -> Polygon | MultiPolygon:
    return space.buffer(-width / 2.0, join_style="mitre")


class ErosionCache:
    """Сужения свободной области по ширине в сантиметрах, общие для всех маршрутов плана."""

    def __init__(self, space: Polygon | MultiPolygon) -> None:
        self.space = space
        self._cache: dict[int, Polygon | MultiPolygon] = {}

    def at(self, width_cm: int) -> Polygon | MultiPolygon:
        if width_cm not in self._cache:
            self._cache[width_cm] = _eroded(self.space, width_cm / 100.0)
        return self._cache[width_cm]


def _width_between(cache: ErosionCache, start: Point, goal: Point) -> tuple[float, bool]:
    if not _connected(cache.at(2), start, goal, 0.01):
        return 0.0, False
    if _connected(cache.at(ROUTE_WIDTH_CAP_CM), start, goal, ROUTE_WIDTH_CAP_CM / 200.0):
        return ROUTE_WIDTH_CAP_CM / 100.0, True
    low, high = 2, ROUTE_WIDTH_CAP_CM
    while high - low > 1:
        mid = (low + high) // 2
        eroded = cache.at(mid)
        if not eroded.is_empty and _connected(eroded, start, goal, mid / 200.0):
            low = mid
        else:
            high = mid
    return round(low / 100.0, 2), True


def route_width(
    plan: Plan, start: tuple[float, float], goal: tuple[float, float], exclude: set[str]
) -> tuple[float, bool]:
    """Ширина самого узкого места на лучшем пути между двумя точками.

    Возвращает (ширина, достижима ли цель вообще).
    """
    space = free_space(plan, exclude)
    if space is None or space.is_empty:
        return 0.0, False
    return _width_between(ErosionCache(space), Point(start), Point(goal))


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
    """Фактическая ширина пути от входа до каждого объекта обслуживания посетителей.

    Точка назначения лежит перед объектом, со стороны посетителя, поэтому сам объект не мешает
    к ней подойти, и сужения свободной области общие для всех маршрутов: каждое ширина
    вычисляется один раз на план, а не для каждого объекта заново.
    """
    start = entrance_point(plan)
    targets = core.equipment_by_type(plan, *target_types)
    if start is None or not targets:
        return []
    space = free_space(plan)
    if space is None or space.is_empty:
        return [RouteWidth(target_id=t.id, width_m=0.0, reachable=False) for t in targets]
    start_pt = Point(start)
    cache = ErosionCache(space)
    results: list[RouteWidth] = []
    for item in targets:
        goal = Point(client_access_point(plan, item))
        width, reachable = _width_between(cache, start_pt, goal)
        results.append(RouteWidth(target_id=item.id, width_m=width, reachable=reachable))
    return results


def route_summary(plan: Plan, target_types: tuple[str, ...]) -> tuple[float | None, float | None]:
    """Самое узкое и самое широкое место путей от входа за один расчёт маршрутов.

    Оба числа нужны вместе: узкое показывает, доберётся ли посетитель до конкретного окна,
    широкое — пропускную ширину общего участка у входа. Считать их двумя вызовами дорого.
    """
    routes = client_route_widths(plan, target_types)
    if not routes:
        return None, None
    reachable = [r.width_m for r in routes if r.reachable]
    if not reachable:
        return 0.0, None
    narrowest = min(reachable) if len(reachable) == len(routes) else 0.0
    return narrowest, max(reachable)


def widest_route_width(plan: Plan, target_types: tuple[str, ...]) -> float | None:
    """Ширина самого свободного из маршрутов от входа.

    Показывает пропускную ширину общего участка пути у входа: местные сужения у отдельных
    окон на неё не влияют, в отличие от минимума по всем маршрутам.
    """
    routes = [r.width_m for r in client_route_widths(plan, target_types) if r.reachable]
    return max(routes) if routes else None


def bottleneck_blockers(
    plan: Plan,
    target_types: tuple[str, ...],
    goal_width_m: float,
    min_gain_m: float = 0.05,
    max_culprits: int = 4,
) -> dict[float, list[str]]:
    """Объекты, которые создают сужение пути посетителя.

    Поиск жадный: убирается объект, дающий наибольшее расширение узкого места, путь
    пересчитывается, и так до тех пор, пока ширина не достигнет goal_width_m. Если ни один
    объект по отдельности не расширяет путь, значит проход зажат двумя объектами сразу — тогда
    проверяются пары среди объектов, ближайших к маршруту.
    Возвращает соответствие «ширина узкого места → виновники по убыванию вклада».
    """
    routes = client_route_widths(plan, target_types)
    reachable_routes = [r for r in routes if r.reachable]
    if not reachable_routes:
        return {}
    start = entrance_point(plan)
    if start is None:
        return {}
    start_pt = Point(start)
    items = {item.id: item for item in core.equipment_by_type(plan, *target_types)}
    targets_by_width: dict[float, list[str]] = {}
    for r in reachable_routes:
        targets_by_width.setdefault(r.width_m, []).append(r.target_id)
    floor = core.floor_equipment(plan)

    def widened(exclude: set[str], goal: Point) -> float:
        space = free_space(plan, exclude=exclude)
        if space is None or space.is_empty:
            return 0.0
        width, reachable = _width_between(ErosionCache(space), start_pt, goal)
        return width if reachable else 0.0

    base_space = free_space(plan)

    def clearance(target_id: str) -> float:
        """Насколько свободно стоит посетитель у цели: расстояние до ближайшего препятствия."""
        point = Point(client_access_point(plan, items[target_id]))
        if base_space is None or base_space.is_empty or not base_space.contains(point):
            return 0.0
        return base_space.boundary.distance(point)

    result: dict[float, list[str]] = {}
    for width, target_ids in targets_by_width.items():
        # Зондом служит цель с самым свободным подходом: у неё нет собственного сужения у окна,
        # и измеряется общее для группы узкое место, а не местное.
        probe_id = max(target_ids, key=clearance)
        goal = Point(client_access_point(plan, items[probe_id]))
        route_line = LineString([start_pt, goal])

        removed: list[str] = []
        current = width
        while current < goal_width_m and len(removed) < max_culprits:
            suspects = [i for i in floor if i.id != probe_id and i.id not in removed]
            if not suspects:
                break
            gains = {s.id: widened({*removed, s.id}, goal) for s in suspects}
            best_id = max(gains, key=lambda k: gains[k])
            if gains[best_id] >= current + min_gain_m:
                removed.append(best_id)
                current = gains[best_id]
                continue
            nearest = sorted(suspects, key=lambda s: core.equipment_polygon(s).distance(route_line))[:6]
            best_pair: tuple[str, str] | None = None
            best_width = current
            for a in range(len(nearest)):
                for b in range(a + 1, len(nearest)):
                    pair = (nearest[a].id, nearest[b].id)
                    candidate = widened({*removed, *pair}, goal)
                    if candidate >= current + min_gain_m and candidate > best_width:
                        best_pair, best_width = pair, candidate
            if best_pair is None:
                break
            removed.extend(best_pair)
            current = best_width
        result[width] = removed
    return result


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
