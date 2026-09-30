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
from shapely.strtree import STRtree

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


def _floor_fingerprint(plan: Plan, exclude: frozenset[str]) -> tuple:
    """Отпечаток того, от чего зависит свободная площадь: стены и напольные объекты.

    По нему свободная площадь берётся из кэша. Ассистент перебирает сотни расстановок, в каждой
    двигается один объект, а площадь считалась заново по десять раз на вариант.
    """
    walls = _wall_fingerprint(plan)
    items = tuple(
        (item.id, item.type, item.position, item.size_m, item.rotation_deg)
        for item in core.floor_equipment(plan)
        if item.id not in exclude
    )
    return walls, items


def _wall_fingerprint(plan: Plan) -> tuple:
    """Отпечаток оболочки: стены и проёмы. От оборудования не зависит."""
    walls = tuple((w.id, w.type, w.from_, w.to) for w in plan.walls)
    doors = tuple((d.id, d.type, tuple(d.wall)) for d in plan.doors)
    return walls, doors


_FREE_SPACE_CACHE: dict[tuple, Polygon | MultiPolygon | None] = {}
_EROSION_CACHE: dict[tuple, "ErosionCache"] = {}
_ENTRANCE_CACHE: dict[tuple, tuple[float, float] | None] = {}
_CIRCLE_CACHE: dict[tuple, tuple[float, tuple[float, float] | None]] = {}
_CACHE_LIMIT = 256


def _remember(cache: dict, key: tuple, value: object) -> None:
    """Запомнить результат, не давая кэшу расти без предела.

    Ассистент перебирает сотни расстановок за один запрос, и каждая даёт свой ключ. Считать
    заново дороже, чем хранить, но держать всё до конца работы сервиса ни к чему.
    """
    if len(cache) >= _CACHE_LIMIT:
        cache.clear()
    cache[key] = value


def reset_caches() -> None:
    """Забыть всё посчитанное. Нужно замерам времени: иначе второй прогон измеряет кэш."""
    _FREE_SPACE_CACHE.clear()
    _EROSION_CACHE.clear()
    _ENTRANCE_CACHE.clear()
    _CIRCLE_CACHE.clear()
    core.reset_caches()


def free_space(plan: Plan, exclude: set[str] | None = None) -> Polygon | MultiPolygon | None:
    """Площадь пола, свободная от оборудования. Внутренние перегородки считаются препятствием."""
    excluded = frozenset(exclude or ())
    key = _floor_fingerprint(plan, excluded)
    if key in _FREE_SPACE_CACHE:
        return _FREE_SPACE_CACHE[key]
    result = _free_space(plan, excluded)
    _remember(_FREE_SPACE_CACHE, key, result)
    return result


def erosion(plan: Plan, exclude: set[str] | None = None) -> "ErosionCache | None":
    """Сужения свободной области для одной расстановки, общие для всех её маршрутов.

    Одну и ту же расстановку спрашивают многократно: правила считают маршруты, метрики —
    пропускную ширину, ассистент — оценку варианта. Сужение области самая дорогая операция во
    всей геометрии, поэтому его результат живёт дольше одного расчёта.
    """
    excluded = frozenset(exclude or ())
    key = _floor_fingerprint(plan, excluded)
    cached = _EROSION_CACHE.get(key)
    if cached is not None:
        return cached
    space = free_space(plan, excluded)
    if space is None or space.is_empty:
        return None
    cache = ErosionCache(space)
    _remember(_EROSION_CACHE, key, cache)
    return cache


def _free_space(plan: Plan, exclude: frozenset[str]) -> Polygon | MultiPolygon | None:
    shape = core.outline(plan)
    if shape is None:
        return None
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


class Region:
    """Свободная область, разобранная на связные части один раз.

    Связность двух точек — самый частый вопрос во всей геометрии: его задают на каждом шаге
    бинарного поиска ширины для каждого маршрута. Поэтому части области, дерево их габаритов и
    подготовленная геометрия считаются один раз на область, а не на каждый вопрос.
    """

    def __init__(self, geometry: Polygon | MultiPolygon) -> None:
        self.geometry = geometry
        self.parts = [] if geometry.is_empty else (
            list(geometry.geoms) if isinstance(geometry, MultiPolygon) else [geometry]
        )
        self.tree = STRtree(self.parts) if self.parts else None

    @property
    def is_empty(self) -> bool:
        return not self.parts

    def component(self, probe: Point) -> int | None:
        """Номер связной части, в которой лежит точка.

        Сравнивать части по номеру, а не по геометрии: равенство многоугольников на плане в
        сотни объектов стоит дороже всего остального расчёта маршрута.
        """
        if self.tree is None:
            return None
        index = int(self.tree.nearest(probe))
        return index if self.parts[index].distance(probe) < 1e-6 else None

    def reach(self, point: Point, radius: float) -> Point | None:
        """Позиция центра человека, чьё тело радиуса radius накрывает точку.

        Если допустимая область дальше radius, человек этой ширины до точки не доходит. Без
        этого ограничения узкое место у самой цели маскировалось бы притяжением к свободной
        области.
        """
        if self.tree is None:
            return None
        if self.geometry.contains(point):
            return point
        nearest = nearest_points(self.geometry, point)[0]
        if nearest.distance(point) > radius + 1e-6:
            return None
        return nearest

    def connects(self, a: Point, b: Point, radius: float) -> bool:
        first_pos, second_pos = self.reach(a, radius), self.reach(b, radius)
        if first_pos is None or second_pos is None:
            return False
        first = self.component(first_pos)
        return first is not None and first == self.component(second_pos)


def _eroded(space: Polygon | MultiPolygon, width: float) -> Polygon | MultiPolygon:
    return space.buffer(-width / 2.0, join_style="mitre")


class ErosionCache:
    """Сужения свободной области по ширине в сантиметрах, общие для всех маршрутов плана."""

    def __init__(self, space: Polygon | MultiPolygon) -> None:
        self.space = space
        self._cache: dict[int, Region] = {}
        self._widths: dict[tuple[float, float, float, float], tuple[float, bool]] = {}

    def at(self, width_cm: int) -> Region:
        region = self._cache.get(width_cm)
        if region is None:
            region = Region(_eroded(self.space, width_cm / 100.0))
            self._cache[width_cm] = region
        return region

    def width(self, start: Point, goal: Point) -> tuple[float, bool]:
        """Ширина узкого места между двумя точками. Один и тот же вопрос считается однажды.

        Маршрут от входа к окну спрашивают и правила, и метрики, и ассистент при переборе.
        """
        key = (round(start.x, 3), round(start.y, 3), round(goal.x, 3), round(goal.y, 3))
        known = self._widths.get(key)
        if known is None:
            known = _width_between(self, start, goal)
            self._widths[key] = known
        return known


def _width_between(cache: ErosionCache, start: Point, goal: Point) -> tuple[float, bool]:
    if not cache.at(2).connects(start, goal, 0.01):
        return 0.0, False
    if cache.at(ROUTE_WIDTH_CAP_CM).connects(start, goal, ROUTE_WIDTH_CAP_CM / 200.0):
        return ROUTE_WIDTH_CAP_CM / 100.0, True
    low, high = 2, ROUTE_WIDTH_CAP_CM
    while high - low > 1:
        mid = (low + high) // 2
        eroded = cache.at(mid)
        if not eroded.is_empty and eroded.connects(start, goal, mid / 200.0):
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
    cache = erosion(plan, exclude)
    if cache is None:
        return 0.0, False
    return cache.width(Point(start), Point(goal))


def entrance_point(plan: Plan) -> tuple[float, float] | None:
    """Точка, с которой посетитель начинает путь: главный вход, сдвинутый внутрь помещения."""
    key = (_wall_fingerprint(plan), plan.flow_hints.main_entrance if plan.flow_hints else None)
    if key in _ENTRANCE_CACHE:
        return _ENTRANCE_CACHE[key]
    point = _entrance_point(plan)
    _remember(_ENTRANCE_CACHE, key, point)
    return point


def _entrance_point(plan: Plan) -> tuple[float, float] | None:
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
    cache = erosion(plan)
    if cache is None:
        return [RouteWidth(target_id=t.id, width_m=0.0, reachable=False) for t in targets]
    start_pt = Point(start)
    results: list[RouteWidth] = []
    for item in targets:
        goal = Point(client_access_point(plan, item))
        width, reachable = cache.width(start_pt, goal)
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
        cache = erosion(plan, exclude=exclude)
        if cache is None:
            return 0.0
        width, reachable = cache.width(start_pt, goal)
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


def widths_without(plan: Plan, target_ids: list[str], exclude: set[str]) -> dict[str, float]:
    """Ширина пути от входа к каждой цели, если убрать объекты exclude; 0 — цель недостижима.

    Проверка виновников сужения: если и без них путь к цели узок, значит тесно из-за чего-то
    ещё, например между самими окнами.
    """
    start = entrance_point(plan)
    cache = erosion(plan, exclude=exclude)
    if start is None or cache is None:
        return {}
    items = {item.id: item for item in plan.equipment}
    widths: dict[str, float] = {}
    for target_id in target_ids:
        item = items.get(target_id)
        if item is None:
            continue
        width, reachable = cache.width(Point(start), Point(client_access_point(plan, item)))
        widths[target_id] = width if reachable else 0.0
    return widths


def largest_clear_circle(plan: Plan, zone_types: tuple[str, ...]) -> tuple[float, tuple[float, float] | None]:
    """Диаметр наибольшего свободного круга в клиентских зонах и его центр.

    Нужен для проверки зоны разворота кресла-коляски.
    """
    key = (
        _floor_fingerprint(plan, frozenset()),
        tuple((z.id, z.type, tuple(map(tuple, z.polygon))) for z in plan.zones),
        zone_types,
    )
    if key in _CIRCLE_CACHE:
        return _CIRCLE_CACHE[key]
    result = _largest_clear_circle(plan, zone_types)
    _remember(_CIRCLE_CACHE, key, result)
    return result


def _largest_clear_circle(plan: Plan, zone_types: tuple[str, ...]) -> tuple[float, tuple[float, float] | None]:
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
