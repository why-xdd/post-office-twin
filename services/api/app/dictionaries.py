"""Справочники зон и типов оборудования по умолчанию.

Базой служат каталог из формата заказчика (`equipment_types_catalog`) и «Перечень оборудования
ОПС». План может переопределить или дополнить любой тип через `plan.dictionaries` — справочник,
пришедший с планом, имеет приоритет над встроенным.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from app.models import Plan


class ZoneType(BaseModel):
    code: str
    label: str
    public: bool = Field(description="Доступна ли зона посетителям")


class EquipmentType(BaseModel):
    code: str
    label: str
    group: str
    default_size_m: tuple[float, float]
    floor_object: bool = Field(
        default=True, description="Занимает площадь пола; настенные и настольные — нет"
    )
    allowed_zones: list[str] = Field(default_factory=list)
    capacity_persons: int | None = None
    operator_workplace: bool = Field(
        default=False, description="У объекта есть место сотрудника: касса, стойка выдачи"
    )
    attribute_schema: dict[str, str] = Field(default_factory=dict)


DEFAULT_ZONE_TYPES: list[ZoneType] = [
    ZoneType(code="client_area", label="Клиентская зона", public=True),
    ZoneType(code="waiting", label="Зона ожидания", public=True),
    ZoneType(code="queue_accumulator", label="Накопитель очереди", public=True),
    ZoneType(code="vestibule", label="Тамбур", public=True),
    ZoneType(code="back_office", label="Складская / служебная зона", public=False),
    ZoneType(code="staff_room", label="Помещение персонала", public=False),
]

PUBLIC = ["client_area", "waiting", "queue_accumulator"]
SERVICE = ["back_office", "staff_room"]
INVENTORY = {"serial_number": "string", "last_inspection": "date", "next_inspection": "date"}

DEFAULT_EQUIPMENT_TYPES: list[EquipmentType] = [
    EquipmentType(
        code="cashier_window",
        label="Окно обслуживания (операционная касса)",
        group="Зона обслуживания клиентов",
        default_size_m=(1.2, 0.8),
        allowed_zones=["client_area", "queue_accumulator"],
        operator_workplace=True,
        attribute_schema={"window_number": "number", "services": "string", **INVENTORY},
    ),
    EquipmentType(
        code="pvz_counter",
        label="Стойка выдачи посылок",
        group="Зона обслуживания клиентов",
        default_size_m=(2.0, 0.8),
        allowed_zones=["client_area"],
        operator_workplace=True,
        attribute_schema={"cells": "number", **INVENTORY},
    ),
    EquipmentType(
        code="self_service_kiosk",
        label="Терминал самообслуживания / электронной очереди",
        group="Зона обслуживания клиентов",
        default_size_m=(0.6, 0.6),
        allowed_zones=["client_area", "vestibule"],
        attribute_schema={"ip_address": "string", "usage_per_month": "number", **INVENTORY},
    ),
    EquipmentType(
        code="payment_terminal_stand",
        label="Стойка с терминалом оплаты",
        group="Зона обслуживания клиентов",
        default_size_m=(0.5, 0.5),
        allowed_zones=["client_area"],
        attribute_schema=INVENTORY,
    ),
    EquipmentType(
        code="package_drop_box",
        label="Почтовый ящик для сбора корреспонденции",
        group="Зона обслуживания клиентов",
        default_size_m=(0.5, 0.4),
        allowed_zones=["client_area", "vestibule"],
        attribute_schema=INVENTORY,
    ),
    EquipmentType(
        code="info_display",
        label="Центральное информационное табло",
        group="Зона обслуживания клиентов",
        default_size_m=(1.1, 0.1),
        floor_object=False,
        allowed_zones=PUBLIC,
        attribute_schema={"diagonal_inch": "number", **INVENTORY},
    ),
    EquipmentType(
        code="media_panel",
        label="Медиапанель",
        group="Зона обслуживания клиентов",
        default_size_m=(1.0, 0.1),
        floor_object=False,
        allowed_zones=PUBLIC,
        attribute_schema={"diagonal_inch": "number", **INVENTORY},
    ),
    EquipmentType(
        code="waiting_seat",
        label="Скамья / кресла для ожидания",
        group="Зона обслуживания клиентов",
        default_size_m=(2.0, 0.5),
        allowed_zones=["waiting", "client_area"],
        capacity_persons=4,
    ),
    EquipmentType(
        code="atm",
        label="Банкомат",
        group="Зона обслуживания клиентов",
        default_size_m=(0.7, 0.5),
        allowed_zones=["client_area", "vestibule"],
        attribute_schema=INVENTORY,
    ),
    EquipmentType(
        code="parcel_locker",
        label="Почтомат",
        group="Зона обслуживания клиентов",
        default_size_m=(1.8, 0.6),
        allowed_zones=["client_area", "vestibule"],
        attribute_schema={"cells": "number", "parcels_loaded": "number", **INVENTORY},
    ),
    EquipmentType(
        code="back_office_workplace",
        label="Рабочее место сотрудника",
        group="Рабочее место оператора",
        default_size_m=(1.2, 0.8),
        allowed_zones=SERVICE,
        operator_workplace=True,
        attribute_schema={"computer": "string", **INVENTORY},
    ),
    EquipmentType(
        code="printer",
        label="Принтер / МФУ",
        group="Рабочее место оператора",
        default_size_m=(0.5, 0.4),
        floor_object=False,
        allowed_zones=[*PUBLIC, *SERVICE],
        attribute_schema={"vendor": "string", "model": "string", **INVENTORY},
    ),
    EquipmentType(
        code="label_printer",
        label="Принтер для этикеток",
        group="Рабочее место оператора",
        default_size_m=(0.25, 0.2),
        floor_object=False,
        allowed_zones=[*PUBLIC, *SERVICE],
        attribute_schema=INVENTORY,
    ),
    EquipmentType(
        code="camera",
        label="Камера видеонаблюдения",
        group="Безопасность и коммуникации",
        default_size_m=(0.2, 0.2),
        floor_object=False,
        allowed_zones=[*PUBLIC, *SERVICE],
        attribute_schema={"ip_address": "string", **INVENTORY},
    ),
    EquipmentType(
        code="fire_alarm",
        label="Пожарная сигнализация (извещатель, АУОП)",
        group="Безопасность и коммуникации",
        default_size_m=(0.2, 0.2),
        floor_object=False,
        allowed_zones=[*PUBLIC, *SERVICE],
        attribute_schema=INVENTORY,
    ),
    EquipmentType(
        code="security_alarm",
        label="Охранно-тревожная сигнализация",
        group="Безопасность и коммуникации",
        default_size_m=(0.3, 0.2),
        floor_object=False,
        allowed_zones=[*PUBLIC, *SERVICE],
        attribute_schema=INVENTORY,
    ),
    EquipmentType(
        code="storage_rack",
        label="Шкаф / стеллаж для посылок",
        group="Вспомогательные элементы",
        default_size_m=(2.0, 0.6),
        allowed_zones=SERVICE,
        attribute_schema={"shelves": "number", **INVENTORY},
    ),
    EquipmentType(
        code="goods_rack",
        label="Стеллаж для товаров",
        group="Вспомогательные элементы",
        default_size_m=(1.0, 0.4),
        allowed_zones=["client_area"],
    ),
    EquipmentType(
        code="weighing_station",
        label="Весы",
        group="Вспомогательные элементы",
        default_size_m=(0.6, 0.6),
        allowed_zones=["client_area", "back_office"],
        attribute_schema=INVENTORY,
    ),
    EquipmentType(
        code="info_stand",
        label="Информационный стенд / навигация",
        group="Вспомогательные элементы",
        default_size_m=(1.0, 0.2),
        allowed_zones=PUBLIC,
    ),
    EquipmentType(
        code="safe",
        label="Сейф",
        group="Вспомогательные элементы",
        default_size_m=(0.6, 0.6),
        allowed_zones=SERVICE,
        attribute_schema=INVENTORY,
    ),
    EquipmentType(
        code="fitting_room",
        label="Примерочная",
        group="Вспомогательные элементы",
        default_size_m=(1.2, 1.2),
        allowed_zones=["client_area"],
    ),
    EquipmentType(
        code="trash_bin",
        label="Урна",
        group="Вспомогательные элементы",
        default_size_m=(0.4, 0.4),
        allowed_zones=[*PUBLIC, *SERVICE],
    ),
    EquipmentType(
        code="potted_plant",
        label="Растение",
        group="Вспомогательные элементы",
        default_size_m=(0.5, 0.5),
        allowed_zones=PUBLIC,
    ),
]


def zone_types(plan: Plan | None = None) -> dict[str, ZoneType]:
    result = {z.code: z for z in DEFAULT_ZONE_TYPES}
    if plan is not None and plan.dictionaries is not None:
        for custom in plan.dictionaries.zone_types:
            base = result.get(custom.code)
            data = {**(base.model_dump() if base else {"public": True}), **custom.model_dump()}
            result[custom.code] = ZoneType.model_validate(data)
    return result


def equipment_types(plan: Plan | None = None) -> dict[str, EquipmentType]:
    result = {t.code: t for t in DEFAULT_EQUIPMENT_TYPES}
    if plan is not None and plan.dictionaries is not None:
        for custom in plan.dictionaries.equipment_types:
            base = result.get(custom.code)
            data = base.model_dump() if base else {
                "group": "Пользовательские типы",
                "default_size_m": custom.default_size_m or (1.0, 1.0),
            }
            # Пустые поля своего типа не затирают значения базового типа.
            data.update({k: v for k, v in custom.model_dump().items() if v not in (None, {}, [])})
            result[custom.code] = EquipmentType.model_validate(data)
    return result


def is_floor_object(plan: Plan, type_code: str) -> bool:
    """Неизвестный тип считается напольным: лучше лишняя проверка, чем пропущенное пересечение."""
    known = equipment_types(plan).get(type_code)
    return True if known is None else known.floor_object
