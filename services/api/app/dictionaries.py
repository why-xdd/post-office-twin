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
    height_m: float = Field(default=1.0, gt=0, description="Высота объекта — для объёмного вида")
    mount_height_m: float = Field(
        default=0.0, ge=0, description="Высота низа над полом: у настенных и настольных больше нуля"
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

# Высоты вынесены отдельной таблицей: в «Перечне оборудования ОПС» их нет, это наши значения по
# типовым габаритам мебели и техники. Первое число — высота объекта, второе — высота его низа над
# полом: у настенного табло и камеры она больше нуля. План может переопределить любую из них
# через свой справочник или через height_m у конкретного объекта.
HEIGHTS: dict[str, tuple[float, float]] = {
    "cashier_window": (1.15, 0.0),
    "pvz_counter": (1.1, 0.0),
    "self_service_kiosk": (1.5, 0.0),
    "payment_terminal_stand": (1.2, 0.0),
    "package_drop_box": (1.3, 0.0),
    "info_display": (0.65, 2.1),
    "media_panel": (0.8, 1.35),
    "waiting_seat": (0.85, 0.0),
    "atm": (1.6, 0.0),
    "parcel_locker": (2.0, 0.0),
    "back_office_workplace": (0.75, 0.0),
    "printer": (0.45, 0.75),
    "label_printer": (0.25, 0.9),
    "camera": (0.15, 2.6),
    "fire_alarm": (0.1, 2.85),
    "fire_extinguisher": (0.6, 0.9),
    "security_alarm": (0.15, 2.2),
    "storage_rack": (2.0, 0.0),
    "goods_rack": (1.8, 0.0),
    "weighing_station": (0.9, 0.0),
    "info_stand": (1.8, 0.0),
    "safe": (0.9, 0.0),
    "fitting_room": (2.1, 0.0),
    "trash_bin": (0.7, 0.0),
    "potted_plant": (1.1, 0.0),
}

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
        code="fire_extinguisher",
        label="Огнетушитель",
        group="Безопасность и коммуникации",
        default_size_m=(0.2, 0.2),
        floor_object=False,
        allowed_zones=[*PUBLIC, *SERVICE, "vestibule"],
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

for _type in DEFAULT_EQUIPMENT_TYPES:
    _height, _mount = HEIGHTS.get(_type.code, (1.0, 0.0))
    _type.height_m, _type.mount_height_m = _height, _mount


_BASE_ZONE_TYPES: dict[str, ZoneType] = {z.code: z for z in DEFAULT_ZONE_TYPES}
_BASE_EQUIPMENT_TYPES: dict[str, EquipmentType] = {t.code: t for t in DEFAULT_EQUIPMENT_TYPES}
_BASE_FLOOR_OBJECT: dict[str, bool] = {t.code: t.floor_object for t in DEFAULT_EQUIPMENT_TYPES}


def zone_types(plan: Plan | None = None) -> dict[str, ZoneType]:
    if plan is None or plan.dictionaries is None or not plan.dictionaries.zone_types:
        return dict(_BASE_ZONE_TYPES)
    result = dict(_BASE_ZONE_TYPES)
    if plan is not None and plan.dictionaries is not None:
        for custom in plan.dictionaries.zone_types:
            base = result.get(custom.code)
            data = {**(base.model_dump() if base else {"public": True}), **custom.model_dump()}
            result[custom.code] = ZoneType.model_validate(data)
    return result


def _has_custom_equipment(plan: Plan | None) -> bool:
    return plan is not None and plan.dictionaries is not None and bool(plan.dictionaries.equipment_types)


def equipment_types(plan: Plan | None = None) -> dict[str, EquipmentType]:
    if not _has_custom_equipment(plan):
        return dict(_BASE_EQUIPMENT_TYPES)
    result = dict(_BASE_EQUIPMENT_TYPES)
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
    if not _has_custom_equipment(plan):
        # Спрашивают на каждый объект плана при каждом расчёте: справочник здесь не пересобирается.
        return _BASE_FLOOR_OBJECT.get(type_code, True)
    known = equipment_types(plan).get(type_code)
    return True if known is None else known.floor_object
