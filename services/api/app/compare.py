"""Сравнение двух вариантов планировки и смета перехода от одного к другому.

Что изменилось (поставлено, убрано, переставлено, перенесены стены), как изменились нормы, проходы
и очередь — и сколько это стоит. Цен в сервисе нет: их вводит человек, который знает расценки
своего региона и подрядчика. Строка без цены остаётся в смете с количеством, а итог честно
помечается неполным — сервис не подставляет выдуманные числа.
"""

from __future__ import annotations

import math
from typing import Annotated, Any

from pydantic import BaseModel, Field

from app.assistant import recommend
from app.dictionaries import equipment_types
from app.models import Plan
from app.rules.catalog import Category
from app.rules.engine import Severity, validate

MOVE_TOLERANCE_M = 0.05
ROTATE_TOLERANCE_DEG = 1.0


# Цена — неотрицательное конечное число: отрицательная превращала смету в доход, а «inf» — в мусор.
Price = Annotated[float, Field(ge=0, le=1e10, allow_inf_nan=False)]


class Prices(BaseModel):
    move: Price | None = Field(default=None, description="Перестановка одного объекта, ₽")
    remove: Price | None = Field(default=None, description="Демонтаж одного объекта, ₽")
    install: Price | None = Field(default=None, description="Монтаж нового объекта, ₽ за штуку")
    wall_per_m: Price | None = Field(default=None, description="Новая или перенесённая перегородка, ₽ за метр")
    wall_removal_per_m: Price | None = Field(default=None, description="Демонтаж перегородки, ₽ за метр")
    equipment: dict[str, Price] = Field(default_factory=dict, description="Цена нового оборудования по коду типа, ₽")


class Item(BaseModel):
    id: str
    label: str
    type: str


class Line(BaseModel):
    text: str
    quantity: float
    unit: str
    price: float | None
    total: float | None
    key: str = Field(description="Ключ цены: move, remove, install, wall_per_m или equipment:<тип>")


class Metric(BaseModel):
    key: str
    label: str
    before: float | int | None
    after: float | int | None
    unit: str = ""
    better: bool | None = None


class Comparison(BaseModel):
    added: list[Item]
    removed: list[Item]
    moved: list[Item]
    walls_changed_m: float
    walls_removed_m: float = Field(default=0.0, description="Метры перегородок, которых в новом варианте нет")
    metrics: list[Metric]
    lines: list[Line]
    total: float
    complete: bool
    missing: list[str]


def _label(plan: Plan, item: Any) -> str:
    known = equipment_types(plan).get(item.type)
    return item.label or (known.label if known else item.type)


def _moved(a: Any, b: Any) -> bool:
    dx = a.position[0] - b.position[0]
    dy = a.position[1] - b.position[1]
    turn = abs(((a.rotation_deg or 0) - (b.rotation_deg or 0) + 180) % 360 - 180)
    return math.hypot(dx, dy) > MOVE_TOLERANCE_M or turn > ROTATE_TOLERANCE_DEG


def _wall_key(wall: Any) -> tuple:
    ends = sorted([tuple(round(v, 2) for v in wall.from_), tuple(round(v, 2) for v in wall.to)])
    return tuple(ends)


def _walls_new(before: Plan, after: Plan) -> float:
    """Метры стен, которых в прежнем варианте не было (новые и перенесённые)."""
    old = {_wall_key(w) for w in before.walls}
    length = 0.0
    for wall in after.walls:
        if _wall_key(wall) not in old:
            length += math.dist(wall.from_, wall.to)
    return round(length, 2)


def _walls_gone(before: Plan, after: Plan) -> float:
    """Метры перегородок, которых в новом варианте нет: их демонтируют. Наружные стены не сносят."""
    kept = {_wall_key(w) for w in after.walls}
    length = 0.0
    for wall in before.walls:
        if wall.type != "outer" and _wall_key(wall) not in kept:
            length += math.dist(wall.from_, wall.to)
    return round(length, 2)


def _category_errors(plan: Plan) -> dict[str, int]:
    report = validate(plan, explain=False)
    counts = {"fire": 0, "accessibility": 0}
    for finding in report.findings:
        if finding.severity is Severity.ERROR and finding.category in (Category.FIRE, Category.ACCESSIBILITY):
            counts[finding.category.value] += 1
    return counts


def _metrics(before: Plan, after: Plan) -> list[Metric]:
    a, b = recommend.summarize(before), recommend.summarize(after)
    ca, cb = _category_errors(before), _category_errors(after)

    def metric(key: str, label: str, x: Any, y: Any, unit: str = "", lower_is_better: bool | None = True) -> Metric:
        better = None
        if lower_is_better is not None and isinstance(x, (int, float)) and isinstance(y, (int, float)) and x != y:
            better = (y < x) if lower_is_better else (y > x)
        return Metric(key=key, label=label, before=x, after=y, unit=unit, better=better)

    rows = [
        metric("errors", "Нарушений норм", a["errors"], b["errors"]),
        metric("fire", "— из них пожарная безопасность", ca["fire"], cb["fire"]),
        metric("accessibility", "— из них доступность для МГН", ca["accessibility"], cb["accessibility"]),
        metric("warnings", "Замечаний", a["warnings"], b["warnings"]),
        metric("entrance", "Свободный путь от входа", a["entrance_route_width"], b["entrance_route_width"], "м", False),
        metric("narrowest", "Узкое место на пути к окнам", a["min_route_width"], b["min_route_width"], "м", False),
        metric("waiting", "Мест в зоне ожидания", a["waiting_capacity"], b["waiting_capacity"], "чел.", False),
        metric("free", "Свободная площадь", a["free_area"], b["free_area"], "м²", None),
    ]
    for name in dict.fromkeys([*a["waits"], *b["waits"]]):
        rows.append(metric(f"wait:{name}", f"Ожидание в пик — {name.lower()}", a["waits"].get(name), b["waits"].get(name), "мин"))
    for row in rows:
        for side in ("before", "after"):
            value = getattr(row, side)
            if isinstance(value, float):
                setattr(row, side, round(value, 2))
    return [r for r in rows if not (r.before is None and r.after is None)]


def _line(text: str, quantity: float, unit: str, price: float | None, key: str) -> Line:
    total = None if price is None else round(price * quantity, 2)
    return Line(text=text, quantity=quantity, unit=unit, price=price, total=total, key=key)


def compare(before: Plan, after: Plan, prices: Prices) -> Comparison:
    old = {e.id: e for e in before.equipment}
    new = {e.id: e for e in after.equipment}
    added = [Item(id=e.id, label=_label(after, e), type=e.type) for e in after.equipment if e.id not in old]
    removed = [Item(id=e.id, label=_label(before, e), type=e.type) for e in before.equipment if e.id not in new]
    moved = [
        Item(id=e.id, label=_label(after, e), type=e.type)
        for e in after.equipment
        if e.id in old and _moved(old[e.id], e)
    ]
    walls = _walls_new(before, after)
    removed_walls = _walls_gone(before, after)

    lines: list[Line] = []
    if moved:
        lines.append(_line("Перестановка оборудования", len(moved), "шт.", prices.move, "move"))
    if removed:
        lines.append(_line("Демонтаж оборудования", len(removed), "шт.", prices.remove, "remove"))
    if added:
        lines.append(_line("Монтаж нового оборудования", len(added), "шт.", prices.install, "install"))
        by_type: dict[str, list[Item]] = {}
        for item in added:
            by_type.setdefault(item.type, []).append(item)
        types = equipment_types(after)
        for type_code, items in by_type.items():
            name = types[type_code].label if type_code in types else type_code
            lines.append(
                _line(f"Покупка: {name}", len(items), "шт.", prices.equipment.get(type_code), f"equipment:{type_code}")
            )
    if removed_walls > 0:
        lines.append(
            _line("Демонтаж перегородок", removed_walls, "м", prices.wall_removal_per_m, "wall_removal_per_m")
        )
    if walls > 0:
        lines.append(_line("Новые и перенесённые перегородки", walls, "м", prices.wall_per_m, "wall_per_m"))

    missing = [line.text for line in lines if line.price is None]
    return Comparison(
        added=added,
        removed=removed,
        moved=moved,
        walls_changed_m=walls,
        walls_removed_m=removed_walls,
        metrics=_metrics(before, after),
        lines=lines,
        total=round(sum(line.total or 0 for line in lines), 2),
        complete=not missing,
        missing=missing,
    )
