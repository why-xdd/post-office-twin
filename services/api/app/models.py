"""Схема цифрового плана отделения.

Надмножество формата из data/example_plan.json: файл заказчика загружается без конвертации,
дополнительные поля (паспорт оборудования, персонал, ссылки в учётные системы) опциональны.

Единицы измерения: метры. Начало координат — левый нижний угол габаритного прямоугольника,
ось X вправо, ось Y вверх. Для оборудования position — центр объекта, size_m — габариты по
осям X и Y до поворота, rotation_deg — поворот вокруг центра по часовой стрелке.
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

Point = Annotated[tuple[float, float], Field(description="Координаты [x, y] в метрах")]


class Base(BaseModel):
    model_config = ConfigDict(extra="allow", populate_by_name=True)


class BBox(Base):
    width_m: float = Field(gt=0)
    height_m: float = Field(gt=0)


class OpsMeta(Base):
    """Паспорт отделения. type определяет, какой норматив охвата применяется."""

    id: str
    type: str | None = None
    type_label: str | None = None
    name: str | None = None
    total_area_sqm: float | None = Field(default=None, gt=0)
    bbox: BBox | None = None
    scale: str | None = None
    ceiling_height_m: float | None = Field(default=None, gt=0)
    settlement_population: int | None = Field(
        default=None,
        ge=0,
        description="Население населённого пункта, тыс. чел. — для проверки по Приказу № 538",
    )
    working_hours: float | None = Field(
        default=None, gt=0, le=24, description="Часов работы в день — для расчёта потока"
    )
    peak_to_average: float | None = Field(
        default=None, ge=1, description="Во сколько раз поток в час пик выше среднего за день"
    )
    service_time_min: float | None = Field(
        default=None, gt=0, description="Среднее время обслуживания у окна, если нет в карточках"
    )


class Wall(Base):
    id: str
    from_: Point = Field(alias="from")
    to: Point
    type: Literal["outer", "partition", "internal"] | str = "outer"
    label: str | None = None


class Door(Base):
    id: str
    wall: tuple[float, float, float, float] = Field(
        description="Отрезок стены [x1, y1, x2, y2], на котором расположен проём"
    )
    type: Literal["entrance", "service", "internal"] | str = "internal"
    width_m: float = Field(gt=0)
    label: str | None = None


class Window(Base):
    id: str
    wall: tuple[float, float, float, float]
    height_m: float | None = Field(default=None, gt=0)


class Zone(Base):
    id: str
    type: str
    label: str | None = None
    polygon: list[Point] = Field(min_length=3)
    area_sqm: float | None = Field(default=None, gt=0)
    expected_capacity_persons: int | None = Field(default=None, ge=0)


class Inventory(Base):
    """Учётные атрибуты объекта: инвентарный номер, сроки обслуживания."""

    serial_number: str | None = None
    vendor: str | None = None
    model: str | None = None
    last_inspection: str | None = None
    next_inspection: str | None = None


class Staff(Base):
    """Сотрудник, закреплённый за рабочим местом."""

    full_name: str | None = None
    position: str | None = None
    personnel_number: str | None = None
    schedule: str | None = None
    avg_service_time_min: float | None = Field(default=None, gt=0)


class Equipment(Base):
    id: str
    type: str
    label: str | None = None
    position: Point
    size_m: tuple[float, float]
    rotation_deg: float = 0.0
    zone: str | None = None
    operator_position: Point | None = Field(
        default=None, description="Рабочее место оператора с служебной стороны окна"
    )
    capacity_persons: int | None = Field(default=None, ge=0)
    height_m: float | None = Field(default=None, gt=0)
    counter_height_m: float | None = Field(
        default=None, gt=0, description="Высота низа окна/стойки обслуживания"
    )
    attributes: dict[str, object] = Field(default_factory=dict)
    inventory: Inventory | None = None
    staff: Staff | None = None
    integrations: dict[str, str] = Field(
        default_factory=dict, description="Ссылки во внешние системы: WFM, DPA, SOM, портал"
    )


class TypicalPath(Base):
    label: str | None = None
    waypoints: list[Point] = Field(min_length=2)
    frequency_share: float = Field(default=0.0, ge=0, le=1)


class FlowHints(Base):
    main_entrance: str | None = None
    typical_paths: list[TypicalPath] = Field(default_factory=list)
    typical_visit_duration_min: float | None = Field(default=None, gt=0)
    peak_hours_local: list[str] = Field(default_factory=list)
    avg_daily_visitors: int | None = Field(default=None, ge=0)


class ZoneTypeDef(Base):
    code: str
    label: str


class EquipmentTypeDef(Base):
    code: str
    label: str
    default_size_m: tuple[float, float] | None = None
    attribute_schema: dict[str, str] = Field(
        default_factory=dict, description="Имя атрибута -> тип: string, number, date, bool"
    )


class Dictionaries(Base):
    """Справочники зон, типов оборудования и атрибутов.

    Хранятся в самом плане, поэтому расширяются пользователем без изменения кода.
    """

    zone_types: list[ZoneTypeDef] = Field(default_factory=list)
    equipment_types: list[EquipmentTypeDef] = Field(default_factory=list)


class Plan(Base):
    ops: OpsMeta
    walls: list[Wall] = Field(default_factory=list)
    doors: list[Door] = Field(default_factory=list)
    windows: list[Window] = Field(default_factory=list)
    zones: list[Zone] = Field(default_factory=list)
    equipment: list[Equipment] = Field(default_factory=list)
    flow_hints: FlowHints | None = None
    dictionaries: Dictionaries | None = None

    @model_validator(mode="after")
    def _collect_duplicate_ids(self) -> Plan:
        """Дубли идентификаторов не мешают загрузить план — их показывает проверка.

        Загрузка не должна падать на некорректном файле: пользователь обязан увидеть план
        и понять, что именно в нём не так.
        """
        return self


def duplicate_ids(plan: Plan) -> dict[str, list[str]]:
    """Идентификаторы, встречающиеся более одного раза, по разделам плана."""
    sections: dict[str, list[str]] = {
        "walls": [w.id for w in plan.walls],
        "doors": [d.id for d in plan.doors],
        "windows": [w.id for w in plan.windows],
        "zones": [z.id for z in plan.zones],
        "equipment": [e.id for e in plan.equipment],
    }
    result: dict[str, list[str]] = {}
    all_ids: list[str] = []
    for name, ids in sections.items():
        seen: set[str] = set()
        dupes = sorted({i for i in ids if i in seen or seen.add(i)})  # type: ignore[func-returns-value]
        if dupes:
            result[name] = dupes
        all_ids.extend(ids)
    seen_global: set[str] = set()
    cross = sorted({i for i in all_ids if i in seen_global or seen_global.add(i)})  # type: ignore[func-returns-value]
    if cross:
        result["__global__"] = cross
    return result
