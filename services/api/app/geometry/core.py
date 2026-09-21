"""Геометрия плана: габариты объектов, зоны, проходы, пути движения.

Все вычисления детерминированы и возвращают не только результат, но и ссылки на объекты, по
которым он получен. Это нужно, чтобы любой вывод проверки можно было показать на плане.

Система координат: метры, начало в левом нижнем углу, X вправо, Y вверх. Для оборудования
position — центр, size_m — габариты по осям до поворота, rotation_deg — поворот вокруг центра
по часовой стрелке.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from shapely.geometry import LineString, Point, Polygon
from shapely.ops import unary_union

from app.dictionaries import is_floor_object
from app.models import Equipment, Plan, Wall, Zone

# Точность округления линейных величин при выводе, метры.
PRECISION = 3


@dataclass(frozen=True)
class Overlap:
    """Пересечение двух физических объектов."""

    first_id: str
    second_id: str
    area_sqm: float


@dataclass(frozen=True)
class Clearance:
    """Свободное расстояние между парой объектов."""

    first_id: str
    second_id: str
    distance_m: float


def equipment_polygon(item: Equipment) -> Polygon:
    """Габаритный прямоугольник объекта с учётом поворота вокруг центра."""
    cx, cy = item.position
    width, height = item.size_m
    half_w, half_h = width / 2.0, height / 2.0
    corners = [(-half_w, -half_h), (half_w, -half_h), (half_w, half_h), (-half_w, half_h)]
    # rotation_deg задан по часовой стрелке, поэтому угол берётся со знаком минус.
    angle = math.radians(-item.rotation_deg)
    cos_a, sin_a = math.cos(angle), math.sin(angle)
    rotated = [
        (cx + x * cos_a - y * sin_a, cy + x * sin_a + y * cos_a) for x, y in corners
    ]
    return Polygon(rotated)


def floor_equipment(plan: Plan) -> list[Equipment]:
    """Оборудование, занимающее площадь пола. Настенные и настольные объекты исключены."""
    return [item for item in plan.equipment if is_floor_object(plan, item.type)]


def zone_polygon(zone: Zone) -> Polygon:
    return Polygon(zone.polygon)


def outline(plan: Plan) -> Polygon | None:
    """Контур помещения по наружным стенам.

    Наружные стены задаются отрезками; замкнутый контур собирается из них. Если стены не
    образуют замкнутую фигуру, возвращается None — это отдельная проверка, а не ошибка расчёта.
    """
    outer: list[Wall] = [w for w in plan.walls if w.type == "outer"]
    if len(outer) < 3:
        return None
    segments = [LineString([w.from_, w.to]) for w in outer]
    merged = unary_union(segments)
    polygons = list(getattr(merged, "geoms", [merged]))
    try:
        from shapely.ops import polygonize

        built = list(polygonize(merged))
    except Exception:  # pragma: no cover - защитный путь
        built = []
    if built:
        return max(built, key=lambda p: p.area)
    if len(polygons) == 1 and isinstance(polygons[0], Polygon):
        return polygons[0]
    return None


def area_of_zone(zone: Zone) -> float:
    return round(zone_polygon(zone).area, PRECISION)


def total_area(plan: Plan) -> float | None:
    shape = outline(plan)
    if shape is None:
        return plan.ops.total_area_sqm
    return round(shape.area, PRECISION)


def occupied_area(plan: Plan) -> float:
    """Площадь пола, занятая оборудованием, без двойного счёта при пересечениях."""
    shapes = [equipment_polygon(item) for item in floor_equipment(plan)]
    if not shapes:
        return 0.0
    return round(unary_union(shapes).area, PRECISION)


def free_area(plan: Plan) -> float | None:
    total = total_area(plan)
    if total is None:
        return None
    return round(max(total - occupied_area(plan), 0.0), PRECISION)


def objects_outside(plan: Plan) -> list[str]:
    """Объекты, выходящие за границы помещения."""
    shape = outline(plan)
    if shape is None:
        return []
    outside: list[str] = []
    for item in plan.equipment:
        poly = equipment_polygon(item)
        if not shape.contains(poly):
            outside.append(item.id)
    return outside


def overlaps(plan: Plan, tolerance_sqm: float = 0.001) -> list[Overlap]:
    """Пары физически пересекающихся объектов.

    Допуск отсекает касания по общей грани: два стеллажа впритык — это не ошибка планировки.
    """
    result: list[Overlap] = []
    items = floor_equipment(plan)
    polygons = [equipment_polygon(item) for item in items]
    for i in range(len(items)):
        for j in range(i + 1, len(items)):
            inter = polygons[i].intersection(polygons[j])
            if not inter.is_empty and inter.area > tolerance_sqm:
                result.append(
                    Overlap(
                        first_id=items[i].id,
                        second_id=items[j].id,
                        area_sqm=round(inter.area, PRECISION),
                    )
                )
    return result


def zone_of_point(plan: Plan, point: tuple[float, float]) -> str | None:
    """Самая точная зона, содержащая точку.

    Функциональные зоны вкладываются друг в друга (зона ожидания лежит внутри клиентской),
    поэтому из подходящих выбирается наименьшая по площади.
    """
    probe = Point(point)
    containing = [z for z in plan.zones if zone_polygon(z).contains(probe)]
    if not containing:
        return None
    return min(containing, key=lambda z: zone_polygon(z).area).id


def mismatched_zone_links(plan: Plan) -> list[dict[str, str | None]]:
    """Объекты, у которых указанная зона не совпадает с фактическим расположением центра."""
    zone_ids = {z.id for z in plan.zones}
    problems: list[dict[str, str | None]] = []
    for item in plan.equipment:
        actual = zone_of_point(plan, item.position)
        if item.zone is not None and item.zone not in zone_ids:
            problems.append({"объект": item.id, "указана": item.zone, "фактически": actual})
        elif item.zone is not None and actual is not None and actual != item.zone:
            problems.append({"объект": item.id, "указана": item.zone, "фактически": actual})
        elif item.zone is None and actual is not None:
            problems.append({"объект": item.id, "указана": None, "фактически": actual})
    return problems


def misplaced_by_type(plan: Plan, allowed: dict[str, list[str]]) -> list[dict[str, object]]:
    """Объекты, стоящие в зоне, куда их тип по справочнику ставить не положено.

    Зоны вложены друг в друга, поэтому объект считается на месте, если хотя бы одна из зон,
    где стоит его центр, есть в списке допустимых. Типы без списка и объекты вне зон не
    проверяются: для вторых есть своя проверка.
    """
    problems: list[dict[str, object]] = []
    for item in plan.equipment:
        permitted = allowed.get(item.type) or []
        if not permitted:
            continue
        probe = Point(item.position)
        here = [z for z in plan.zones if zone_polygon(z).contains(probe)]
        if not here or any(z.type in permitted for z in here):
            continue
        smallest = min(here, key=lambda z: zone_polygon(z).area)
        problems.append({"объект": item.id, "зона": smallest, "допустимые": permitted})
    return problems


def clearances(plan: Plan, limit_m: float) -> list[Clearance]:
    """Пары объектов, между которыми расстояние меньше заданного.

    Используется для проверки проходов: норматив задаёт минимальную ширину прохода между
    рабочими местами и между оборудованием.
    """
    tight: list[Clearance] = []
    items = floor_equipment(plan)
    polygons = [equipment_polygon(item) for item in items]
    for i in range(len(items)):
        for j in range(i + 1, len(items)):
            distance = polygons[i].distance(polygons[j])
            if 0 < distance < limit_m:
                tight.append(
                    Clearance(
                        first_id=items[i].id,
                        second_id=items[j].id,
                        distance_m=round(distance, PRECISION),
                    )
                )
    return tight


def wall_clearances(plan: Plan, limit_m: float) -> list[Clearance]:
    """Объекты, стоящие к стене ближе допустимого."""
    tight: list[Clearance] = []
    walls = [(w.id, LineString([w.from_, w.to])) for w in plan.walls]
    for item in floor_equipment(plan):
        poly = equipment_polygon(item)
        for wall_id, line in walls:
            distance = poly.distance(line)
            if 0 < distance < limit_m:
                tight.append(
                    Clearance(
                        first_id=item.id,
                        second_id=wall_id,
                        distance_m=round(distance, PRECISION),
                    )
                )
    return tight


def door_center(wall: tuple[float, float, float, float]) -> tuple[float, float]:
    x1, y1, x2, y2 = wall
    return ((x1 + x2) / 2.0, (y1 + y2) / 2.0)


DOOR_CLEAR_DEPTH_M = 0.6


def door_clear_area(wall: tuple[float, float, float, float]) -> Polygon:
    """Полоса шириной с проём и глубиной в ширину плеч проходящего человека по обе стороны двери."""
    x1, y1, x2, y2 = wall
    length = max(((x2 - x1) ** 2 + (y2 - y1) ** 2) ** 0.5, 1e-9)
    nx, ny = -(y2 - y1) / length * DOOR_CLEAR_DEPTH_M, (x2 - x1) / length * DOOR_CLEAR_DEPTH_M
    return Polygon([(x1 + nx, y1 + ny), (x2 + nx, y2 + ny), (x2 - nx, y2 - ny), (x1 - nx, y1 - ny)])


def seat_in_door_swing(door_wall: tuple[float, float, float, float], width_m: float, seat: tuple[float, float]) -> bool:
    """Место оператора в зоне открывания двери.

    Полотно описывает четверть круга радиусом в ширину проёма; в какую сторону открывается
    дверь, план не хранит, поэтому зона берётся с обеих сторон.
    """
    x1, y1, x2, y2 = door_wall
    return LineString([(x1, y1), (x2, y2)]).distance(Point(seat)) < max(width_m, DOOR_CLEAR_DEPTH_M)


def blocked_doors(plan: Plan) -> list[tuple[str, list[str]]]:
    """Двери, проём которых загорожен оборудованием или местом оператора."""
    result: list[tuple[str, list[str]]] = []
    floor = floor_equipment(plan)
    for door in plan.doors:
        area = door_clear_area(door.wall)
        blockers = [
            item.id
            for item in floor
            if equipment_polygon(item).intersection(area).area > 0.02
            or (
                item.operator_position is not None
                and seat_in_door_swing(door.wall, door.width_m, item.operator_position)
            )
        ]
        if blockers:
            result.append((door.id, blockers))
    return result


def distance_from_entrance(plan: Plan, item: Equipment) -> float | None:
    """Расстояние от объекта до главного входа по прямой.

    Применяется к правилу размещения терминала электронной очереди: он должен стоять у входа.
    """
    entrance = None
    preferred = plan.flow_hints.main_entrance if plan.flow_hints else None
    for door in plan.doors:
        if preferred and door.id == preferred:
            entrance = door
            break
        if entrance is None and door.type == "entrance":
            entrance = door
    if entrance is None:
        return None
    center = Point(door_center(entrance.wall))
    return round(equipment_polygon(item).distance(center), PRECISION)


def equipment_by_type(plan: Plan, *types: str) -> list[Equipment]:
    wanted = set(types)
    return [item for item in plan.equipment if item.type in wanted]


def zone_by_type(plan: Plan, *types: str) -> list[Zone]:
    wanted = set(types)
    return [zone for zone in plan.zones if zone.type in wanted]
