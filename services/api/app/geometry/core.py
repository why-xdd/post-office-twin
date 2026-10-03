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
from functools import lru_cache

from shapely.geometry import LineString, Point, Polygon
from shapely.ops import unary_union
from shapely.validation import make_valid

from app.dictionaries import is_floor_object
from app.models import Equipment, Plan, Wall, Zone

# Точность округления линейных величин при выводе, метры.
PRECISION = 3
# Насколько ненапольный объект может выступать за контур помещения: на толщину стены, на которой
# он висит.
WALL_MOUNT_TOLERANCE_M = 0.35


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


@lru_cache(maxsize=16384)
def _rectangle(cx: float, cy: float, width: float, height: float, rotation_deg: float) -> Polygon:
    """Прямоугольник по центру, габаритам и повороту.

    Кэш здесь потому, что ассистент проверяет сотни расстановок, и в каждой почти все объекты
    стоят на прежних местах: один и тот же прямоугольник иначе строился бы заново тысячи раз.
    Фигуры shapely не меняются после создания, поэтому общий экземпляр безопасен.
    """
    half_w, half_h = width / 2.0, height / 2.0
    corners = [(-half_w, -half_h), (half_w, -half_h), (half_w, half_h), (-half_w, half_h)]
    # rotation_deg задан по часовой стрелке, поэтому угол берётся со знаком минус.
    angle = math.radians(-rotation_deg)
    cos_a, sin_a = math.cos(angle), math.sin(angle)
    return Polygon([(cx + x * cos_a - y * sin_a, cy + x * sin_a + y * cos_a) for x, y in corners])


def equipment_polygon(item: Equipment) -> Polygon:
    """Габаритный прямоугольник объекта с учётом поворота вокруг центра."""
    return _rectangle(item.position[0], item.position[1], item.size_m[0], item.size_m[1], item.rotation_deg)


def floor_equipment(plan: Plan) -> list[Equipment]:
    """Оборудование, занимающее площадь пола. Настенные и настольные объекты исключены."""
    return [item for item in plan.equipment if is_floor_object(plan, item.type)]


@lru_cache(maxsize=4096)
def _zone_shape(points: tuple[tuple[float, float], ...]) -> Polygon:
    """Многоугольник зоны по её точкам; чинится, если сам себя пересекает."""
    shape = Polygon(points)
    if shape.is_valid:
        return shape
    fixed = make_valid(shape)
    if isinstance(fixed, Polygon):
        return fixed
    # Восьмёрка распадается на несколько кусков: берём наибольший, он и есть зона.
    parts = [part for part in getattr(fixed, "geoms", []) if isinstance(part, Polygon)]
    return max(parts, key=lambda part: part.area) if parts else shape.buffer(0)


def zone_polygon(zone: Zone) -> Polygon:
    """Многоугольник зоны. Самопересечение чинится: такой полигон рисуют руками, и падать нельзя.

    Восьмёрка («бабочка») в исходных данных встречается, когда зону обводили мышью и перепутали
    порядок точек. Shapely на таком считать не умеет, поэтому фигура приводится к правильной, а о
    самом факте сообщает отдельное правило проверки.
    """
    return _zone_shape(tuple((float(x), float(y)) for x, y in zone.polygon))


def reset_caches() -> None:
    """Забыть посчитанные фигуры. Нужно замерам времени, чтобы они мерили расчёт, а не кэш."""
    _rectangle.cache_clear()
    _zone_shape.cache_clear()


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
    """Объекты, выходящие за границы помещения.

    Ненапольное оборудование меряется мягче: камера и табло висят на самой стене, извещатель — на
    потолке, принтер стоит на столе. Их габариты выходят за контур по построению, поэтому для них
    проверяется только то, что объект стоит в этом помещении, а не снаружи здания.
    """
    shape = outline(plan)
    if shape is None:
        return []
    outside: list[str] = []
    for item in floor_equipment(plan):
        poly = equipment_polygon(item)
        if not shape.contains(poly):
            outside.append(item.id)
    room = shape.buffer(WALL_MOUNT_TOLERANCE_M)
    for item in plan.equipment:
        if is_floor_object(plan, item.type):
            continue
        if not room.contains(Point(item.position)):
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


OPERATOR_SPACE_M = 0.6


def operator_space_conflicts(plan: Plan, tolerance_sqm: float = 0.005) -> list[tuple[str, str]]:
    """Объекты, занявшие место сотрудника за стойкой: пары (владелец места, помеха).

    Место сотрудника — квадрат 0,6 × 0,6 м вокруг operator_position. Физическим объектом его
    не считают остальные проверки, поэтому без этой скамья могла встать прямо за кассу.
    """
    from shapely.geometry import box as square

    half = OPERATOR_SPACE_M / 2.0
    items = floor_equipment(plan)
    polygons = {item.id: equipment_polygon(item) for item in items}
    conflicts: list[tuple[str, str]] = []
    for owner in plan.equipment:
        if owner.operator_position is None:
            continue
        x, y = owner.operator_position
        seat = square(x - half, y - half, x + half, y + half)
        for item in items:
            if item.id == owner.id:
                continue
            if polygons[item.id].intersection(seat).area > tolerance_sqm:
                conflicts.append((owner.id, item.id))
    return conflicts


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
            # Габариты, наехавшие друг на друга, — это отдельное нарушение «объекты стоят друг
            # на друге»; здесь такие пары не повторяются.
            if polygons[i].intersection(polygons[j]).area > 0.001:
                continue
            # Сравнивается то же округлённое число, что показывается человеку: иначе 0,79999 м
            # попадает в нарушение с текстом «0,8 м при требуемых 0,8 м». Ноль считается: между
            # объектами, поставленными вплотную, прохода нет вовсе.
            if round(distance, PRECISION) < limit_m:
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
            # Сравнивается то же округлённое число, что показывается человеку: иначе 0,79999 м
            # попадает в нарушение с текстом «0,8 м при требуемых 0,8 м». Ноль считается:
            # рабочее место вплотную к стене означает, что оператору некуда встать.
            if round(distance, PRECISION) < limit_m:
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


def door_swing_zone(wall: tuple[float, float, float, float], width_m: float) -> Polygon:
    """Где ходит полотно двери: проём вглубь на ширину двери по обе стороны стены.

    В какую сторону открывается дверь и где петли, план не хранит, поэтому зона берётся с обеих
    сторон и во всю ширину проёма — четверть круга с любой стороны в неё укладывается.
    """
    x1, y1, x2, y2 = wall
    length = max(((x2 - x1) ** 2 + (y2 - y1) ** 2) ** 0.5, 1e-9)
    depth = max(width_m, DOOR_CLEAR_DEPTH_M)
    nx, ny = -(y2 - y1) / length * depth, (x2 - x1) / length * depth
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
