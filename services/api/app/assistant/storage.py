"""Ёмкость хранения посылок: хватит ли мест в кладовой, на стеллажах и в почтомате.

ВНТП 311-98 п. 3.10: кладовая посылок к выдаче рассчитывается на 200 % среднесуточного
входящего объёма плюс 60 % исходящего. Сценарий «завоз ×N» — распродажа маркетплейса или
предпраздничная неделя: входящий поток растёт, и видно, где посылкам станет негде лежать.

Вместимость объекта берётся из его атрибутов (cells — ячейки почтомата или мест ПВЗ, places —
мест на стеллаже). Если её нет, она оценивается по габариту и числу полок, и это помечается.
"""

from __future__ import annotations

import math

from pydantic import BaseModel, Field

from app.models import Equipment, Plan

IN_FACTOR = 2.0
OUT_FACTOR = 0.6
SOURCE = "ВНТП 311-98 «Объекты почтовой связи», п. 3.10"
# Посылка на полке в среднем занимает 0,4 м по длине полки (короб 40×30 см) — допущение.
PARCEL_ON_SHELF_M = 0.4
DEFAULT_SHELVES = 5
# Ячейка почтомата — около 0,3 × 0,3 м фасада.
LOCKER_CELL_M = 0.3
LOCKER_HEIGHT_M = 2.0
STORAGE_TYPES = ("storage_rack", "parcel_locker", "pvz_counter")
# Стеллаж для писем посылки не хранит.
NOT_PARCELS = ("пис", "корресп", "газет")


class StorageItem(BaseModel):
    id: str
    label: str
    places: int
    estimated: bool = Field(description="Вместимость оценена по габариту, а не взята из паспорта")


class StorageReport(BaseModel):
    available: bool
    reason: str | None = None
    factor: float = 1.0
    items: list[StorageItem] = Field(default_factory=list)
    places: int = 0
    incoming_per_day: float | None = None
    outgoing_per_day: float | None = None
    required: int | None = None
    enough: bool | None = None
    deficit: int = 0
    source: str = SOURCE
    advice: list[str] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)


# Больше мест на одном объекте в отделении не бывает: такое число — опечатка в паспорте.
MAX_PLACES = 100_000


def _int(value: object) -> int | None:
    """Целое число мест из атрибута или None, если там не число, бесконечность или чепуха."""
    try:
        number = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError, OverflowError):
        return None
    if not math.isfinite(number) or number < 0 or number > MAX_PLACES:
        return None
    return int(number)


def places_of(item: Equipment) -> tuple[int, bool] | None:
    """Мест для посылок у объекта и оценка ли это. None — объект посылки не хранит."""
    attributes = item.attributes or {}
    label = (item.label or "").lower()
    if item.type == "storage_rack" and any(word in label for word in NOT_PARCELS):
        return None
    for key in ("places", "cells"):
        known = _int(attributes.get(key))
        if known is not None:
            return known, False
    length = max(item.size_m)
    if item.type == "storage_rack":
        shelves = _int(attributes.get("shelves")) or DEFAULT_SHELVES
        return shelves * math.floor(length / PARCEL_ON_SHELF_M), True
    if item.type == "parcel_locker":
        return math.floor(length / LOCKER_CELL_M) * math.floor(LOCKER_HEIGHT_M / LOCKER_CELL_M), True
    return None


def analyze(plan: Plan, factor: float = 1.0) -> StorageReport:
    report = StorageReport(available=True, factor=factor)
    for item in plan.equipment:
        if item.type not in STORAGE_TYPES:
            continue
        found = places_of(item)
        if found is None:
            continue
        places, estimated = found
        report.items.append(
            StorageItem(id=item.id, label=item.label or item.type, places=places, estimated=estimated)
        )
    report.places = sum(i.places for i in report.items)
    if any(i.estimated for i in report.items):
        report.assumptions.append(
            f"Где вместимость не указана в паспорте, она оценена: посылка занимает {PARCEL_ON_SHELF_M:g} м "
            f"полки, по умолчанию {DEFAULT_SHELVES} полок; ячейка почтомата — {LOCKER_CELL_M:g} × "
            f"{LOCKER_CELL_M:g} м фасада."
        )
    incoming = plan.ops.parcels_in_per_day
    outgoing = plan.ops.parcels_out_per_day
    if incoming is None:
        report.available = False
        report.reason = (
            "В паспорте отделения не заданы входящие посылки в день: без них норму ВНТП 311-98 п. 3.10 "
            "не проверить. Заполните «Посылок в день» в свойствах плана."
        )
        return report
    report.incoming_per_day = round(incoming * factor, 1)
    report.outgoing_per_day = outgoing
    if outgoing is None:
        report.assumptions.append("Исходящие посылки не заданы — учтены как 0.")
    report.required = math.ceil(IN_FACTOR * incoming * factor + OUT_FACTOR * (outgoing or 0))
    report.enough = report.places >= report.required
    report.deficit = max(0, report.required - report.places)
    if factor != 1:
        report.assumptions.append(f"Сценарий: входящий поток ×{factor:g} (распродажа, предпраздничная неделя).")
    if report.enough:
        spare = report.places - report.required
        report.advice.append(f"Мест хватает: {report.places} при норме {report.required}, запас {spare}.")
    else:
        rack = 6 * math.floor(2.0 / PARCEL_ON_SHELF_M)
        racks = math.ceil(report.deficit / rack)
        report.advice.append(
            f"Не хватает {report.deficit} мест: есть {report.places} при норме {report.required}. Это примерно "
            f"{racks} {'стеллаж' if racks == 1 else 'стеллажа' if racks < 5 else 'стеллажей'} 2 м по 6 полок "
            f"— место подберёт ассистент в служебной зоне."
        )
    return report
