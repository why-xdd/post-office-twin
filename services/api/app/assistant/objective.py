"""Во что обходится расстановка.

Раньше вариант выбирался по одному числу — количеству ошибок проверки. Из-за этого ассистент
мог убрать скамью из зоны ожидания: ошибок нет, а сидеть негде. Здесь собраны все стороны
качества планировки в одну сравнимую величину: чем меньше, тем лучше. Веса подобраны так, что
нарушение нормы всегда дороже любого удобства, а удобства сравнимы между собой.
"""

from __future__ import annotations

from dataclasses import dataclass

from shapely.geometry import Point

from app.assistant import capacity as capacity_model
from app.geometry import core, passability
from app.models import Plan
from app.rules.catalog import MIN_PATH_WITH_POCKETS_M
from app.rules.engine import SERVICE_WINDOW_TYPES, VISITOR_TARGET_TYPES, validate

# Цена единицы каждой стороны качества, в условных баллах.
ERROR_COST = 100.0
WARNING_COST = 6.0
"""Метр недостающей ширины общего прохода у входа: коляска либо проходит, либо нет."""
NARROW_PATH_COST = 60.0
"""Метр недостающей ширины подхода к конкретному окну — та же беда, но у одной цели."""
NARROW_ROUTE_COST = 45.0
"""Одно потерянное место ожидания — примерно как одно предупреждение."""
SEAT_COST = 5.0
"""Метр среднего пути посетителя от входа до окна обслуживания."""
WALK_COST = 0.6
"""Метр переноса оборудования: из двух равных решений выбирается то, где меньше сдвигов."""
MOVE_COST = 0.25


@dataclass(frozen=True)
class Score:
    """Разложение оценки по слагаемым: по нему же объясняется выбор в рекомендации."""

    errors: int
    warnings: int
    entrance_width_m: float | None
    narrowest_route_m: float | None
    waiting_capacity: int | None
    waiting_needed: int | None
    walk_m: float | None
    moved_m: float
    total: float

    def better_than(self, other: "Score", margin: float = 1e-6) -> bool:
        return self.total < other.total - margin


def walk_distance(plan: Plan) -> float | None:
    """Средний путь по прямой от входа до окон обслуживания — грубая мера удобства зала."""
    entrance = passability.entrance_point(plan)
    windows = core.equipment_by_type(plan, *SERVICE_WINDOW_TYPES)
    if entrance is None or not windows:
        return None
    point = Point(entrance)
    return round(sum(core.equipment_polygon(w).distance(point) for w in windows) / len(windows), 3)


def moved_distance(plan: Plan, reference: Plan | None) -> float:
    """Сумма перемещений оборудования относительно исходного плана."""
    if reference is None:
        return 0.0
    before = {item.id: item.position for item in reference.equipment}
    total = 0.0
    for item in plan.equipment:
        start = before.get(item.id)
        if start is None:
            continue
        total += ((item.position[0] - start[0]) ** 2 + (item.position[1] - start[1]) ** 2) ** 0.5
    return round(total, 3)


def score(
    plan: Plan,
    reference: Plan | None = None,
    *,
    errors: int | None = None,
    warnings: int | None = None,
    entrance_width: float | None = None,
    narrowest_route: float | None = None,
    with_queue: bool = True,
) -> Score:
    """Оценка расстановки. Готовые значения передаются, чтобы не считать их дважды.

    with_queue=False пропускает расчёт очереди по Эрлангу: при переборе сотен кандидатов он
    стоит дороже всего остального вместе взятого, а сравнивать варианты можно и по числу мест.
    """
    if errors is None or warnings is None:
        report = validate(plan, explain=False)
        errors = report.errors if errors is None else errors
        warnings = report.warnings if warnings is None else warnings
    if entrance_width is None or narrowest_route is None:
        computed_narrow, computed_wide = passability.route_summary(plan, VISITOR_TARGET_TYPES)
        entrance_width = computed_wide if entrance_width is None else entrance_width
        narrowest_route = computed_narrow if narrowest_route is None else narrowest_route

    seats = capacity_model.waiting_capacity(plan, [])
    needed = capacity_model.analyze(plan).peak_queue_persons if with_queue else None
    walk = walk_distance(plan)
    moved = moved_distance(plan, reference)

    total = ERROR_COST * errors + WARNING_COST * warnings
    if entrance_width is not None:
        total += NARROW_PATH_COST * max(0.0, MIN_PATH_WITH_POCKETS_M - entrance_width)
    if narrowest_route is not None:
        total += NARROW_ROUTE_COST * max(0.0, MIN_PATH_WITH_POCKETS_M - narrowest_route)
    if seats is not None and needed is not None:
        total += SEAT_COST * max(0, needed - seats)
    elif seats is not None:
        # Поток не задан: вместимость всё равно ценна, но сравниваем только между вариантами.
        total -= SEAT_COST * min(seats, 30) / 10.0
    if walk is not None:
        total += WALK_COST * walk
    total += MOVE_COST * moved

    return Score(
        errors=errors,
        warnings=warnings,
        entrance_width_m=entrance_width,
        narrowest_route_m=narrowest_route,
        waiting_capacity=seats,
        waiting_needed=needed,
        walk_m=walk,
        moved_m=moved,
        total=round(total, 3),
    )
