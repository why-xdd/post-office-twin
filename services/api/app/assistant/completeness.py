"""Комплектность отделения: отсутствующее и лишнее оборудование.

Правила комплектования выведены из «Перечня оборудования ОПС» заказчика. Где перечень не задаёт
количество, количество — принятое допущение, и оно указано в правиле явно, как требует ТЗ.
"""

from __future__ import annotations

import math
from typing import Callable

from pydantic import BaseModel, Field

from app.assistant.capacity import CapacityReport
from app.geometry import core
from app.models import Plan

SOURCE = "Перечень оборудования ОПС"
SERVICE_WINDOWS = ("cashier_window", "pvz_counter")
QUEUE_TERMINALS = ("self_service_kiosk",)


class CompletenessIssue(BaseModel):
    rule_id: str
    kind: str = Field(description="missing — не хватает, excess — лишнее")
    equipment_type: str
    present: int
    required: int
    message: str
    source: str
    clause: str
    assumption: str | None = None
    objects: list[str] = Field(default_factory=list)


class Requirement(BaseModel):
    id: str
    equipment_type: str
    clause: str
    assumption: str | None
    minimum: Callable[[Plan, CapacityReport | None], int]
    maximum: Callable[[Plan, CapacityReport | None], int | None] = lambda _p, _c: None

    model_config = {"arbitrary_types_allowed": True}


def _windows(plan: Plan) -> int:
    return len(core.equipment_by_type(plan, *SERVICE_WINDOWS))


def _functional_parts(plan: Plan) -> int:
    public = {"client_area", "waiting", "queue_accumulator", "vestibule"}
    has_public = any(z.type in public for z in plan.zones)
    has_service = any(z.type not in public for z in plan.zones)
    return max(int(has_public) + int(has_service), 1)


def _seats_needed(plan: Plan, capacity: CapacityReport | None) -> int:
    if capacity is None or capacity.peak_queue_persons is None:
        return 1 if _windows(plan) else 0
    # Посадочные места — для половины пиковой очереди, одна скамья вмещает 4 человека.
    return max(1, math.ceil(capacity.peak_queue_persons / 2 / 4)) if _windows(plan) else 0


REQUIREMENTS: list[Requirement] = [
    Requirement(
        id="queue_terminal",
        equipment_type="self_service_kiosk",
        clause="Зона обслуживания клиентов: «Терминал управления очередью (талонный автомат)»",
        assumption="Терминал очереди нужен при двух и более окнах обслуживания; больше терминалов, "
        "чем окон, не требуется.",
        minimum=lambda p, _c: 1 if _windows(p) >= 2 else 0,
        maximum=lambda p, _c: max(_windows(p), 1),
    ),
    Requirement(
        id="info_display",
        equipment_type="info_display",
        clause="Зона обслуживания клиентов: «Центральное информационное табло (монитор 42–48\" "
        "с номерами окон)»",
        assumption="Табло с номерами окон нужно при двух и более окнах обслуживания.",
        minimum=lambda p, _c: 1 if _windows(p) >= 2 else 0,
    ),
    Requirement(
        id="waiting_seats",
        equipment_type="waiting_seat",
        clause="Зона обслуживания клиентов: «Скамьи / кресла для ожидания»",
        assumption="Сидячие места — для половины очереди в час пик по расчёту пропускной "
        "способности, одна скамья на 4 человек.",
        minimum=_seats_needed,
    ),
    Requirement(
        id="mailbox",
        equipment_type="package_drop_box",
        clause="Зона обслуживания клиентов: «Почтовый ящик для сбора корреспонденции "
        "(в тамбуре или у входа)»",
        assumption=None,
        minimum=lambda _p, _c: 1,
    ),
    Requirement(
        id="scales",
        equipment_type="weighing_station",
        clause="Вспомогательные элементы: «Весы (настольные или напольные)»",
        assumption="Весы нужны, если в отделении есть приём отправлений; одних весов достаточно.",
        minimum=lambda p, _c: 1 if _windows(p) else 0,
    ),
    Requirement(
        id="parcel_storage",
        equipment_type="storage_rack",
        clause="Вспомогательные элементы: «Шкаф/стеллаж для посылок»",
        assumption="При наличии выдачи посылок нужен хотя бы один стеллаж в служебной зоне.",
        minimum=lambda p, _c: 1 if core.equipment_by_type(p, "pvz_counter") else 0,
    ),
    Requirement(
        id="video_camera",
        equipment_type="camera",
        clause="Безопасность и коммуникации: «Камера видеонаблюдения»",
        assumption="Минимум одна камера на отделение.",
        minimum=lambda _p, _c: 1,
    ),
    Requirement(
        id="fire_alarm",
        equipment_type="fire_alarm",
        clause="Безопасность и коммуникации: «Пожарная сигнализация (датчики, АУОП)»",
        assumption="Минимум один извещатель в клиентской части и один в служебной; точное "
        "количество определяет проект пожарной сигнализации по площади и высоте помещений.",
        minimum=lambda p, _c: _functional_parts(p),
    ),
    Requirement(
        id="info_stand",
        equipment_type="info_stand",
        clause="Вспомогательные элементы: «Информационный стенд / навигация»",
        assumption=None,
        minimum=lambda _p, _c: 1,
    ),
]


def analyze(plan: Plan, capacity: CapacityReport | None = None) -> list[CompletenessIssue]:
    issues: list[CompletenessIssue] = []
    for req in REQUIREMENTS:
        items = core.equipment_by_type(plan, req.equipment_type)
        present = len(items)
        minimum = req.minimum(plan, capacity)
        maximum = req.maximum(plan, capacity)
        if present < minimum:
            issues.append(
                CompletenessIssue(
                    rule_id=req.id,
                    kind="missing",
                    equipment_type=req.equipment_type,
                    present=present,
                    required=minimum,
                    message=f"Не хватает оборудования «{req.equipment_type}»: есть {present}, "
                    f"требуется {minimum}.",
                    source=SOURCE,
                    clause=req.clause,
                    assumption=req.assumption,
                )
            )
        elif maximum is not None and present > maximum:
            issues.append(
                CompletenessIssue(
                    rule_id=req.id,
                    kind="excess",
                    equipment_type=req.equipment_type,
                    present=present,
                    required=maximum,
                    message=f"Лишнее оборудование «{req.equipment_type}»: есть {present}, "
                    f"достаточно {maximum}.",
                    source=SOURCE,
                    clause=req.clause,
                    assumption=req.assumption,
                    objects=[item.id for item in items[maximum:]],
                )
            )
    return issues
