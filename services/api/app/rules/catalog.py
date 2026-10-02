"""Реестр правил проверки планировки.

Каждое правило хранит источник и номер пункта, поэтому вывод проверки можно проследить до
документа. Значения вынесены в данные, а не в код: правило можно отключить или изменить
через API, не пересобирая сервис.
"""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field


class Severity(str, Enum):
    ERROR = "error"
    WARNING = "warning"
    INFO = "info"


class Category(str, Enum):
    GEOMETRY = "geometry"
    ACCESSIBILITY = "accessibility"
    TECHNOLOGY = "technology"
    CAPACITY = "capacity"
    FIRE = "fire"


class Rule(BaseModel):
    id: str
    title: str
    category: Category
    severity: Severity
    source: str = Field(description="Документ-основание")
    clause: str = Field(description="Пункт или таблица документа")
    value: float | None = Field(default=None, description="Пороговое значение в метрах или м²")
    unit: str = ""
    rationale: str = Field(description="Что именно проверяется и почему")
    enabled: bool = True


VNTP = "ВНТП 311-98 «Объекты почтовой связи»"
SP59 = "СП 59.13330.2020 «Доступность зданий и сооружений для МГН»"
ORDER_538 = "Приказ Минцифры России от 26.10.2020 № 538"
EQUIPMENT_LIST = "Перечень оборудования ОПС"
SP1 = "СП 1.13130.2020 «Эвакуационные пути и выходы»"
PPR = "Правила противопожарного режима (ПП РФ от 16.09.2020 № 1479)"

DEFAULT_RULES: list[Rule] = [
    Rule(
        id="passage_between_workplaces",
        title="Проход между рабочими местами",
        category=Category.TECHNOLOGY,
        severity=Severity.ERROR,
        source=VNTP,
        clause="табл. 13, п. 4.5",
        value=1.0,
        unit="м",
        rationale="Между рабочими местами операторов требуется проход не менее 1000 мм.",
    ),
    Rule(
        id="clearance_from_wall",
        title="Проход от стены до оборудования",
        category=Category.TECHNOLOGY,
        severity=Severity.WARNING,
        source=VNTP,
        clause="табл. 13, п. 4.5",
        value=0.8,
        unit="м",
        rationale="От стены до границы рабочей зоны и до торца оборудования требуется 800 мм.",
    ),
    Rule(
        id="workplace_area",
        title="Площадь операционных рабочих мест",
        category=Category.TECHNOLOGY,
        severity=Severity.WARNING,
        source=VNTP,
        clause="п. 3.9",
        value=None,
        unit="м²",
        rationale="На рабочее место приёма и выдачи посылок отводится 7,5 м², на место приёма "
        "письменной корреспонденции, бандеролей и подписки — 6,0 м². Допущение: суммарная "
        "потребность сравнивается с площадью служебных зон плана.",
    ),
    Rule(
        id="client_area_per_workplace",
        title="Площадь клиентской зоны на одно операционное место",
        category=Category.CAPACITY,
        severity=Severity.WARNING,
        source=VNTP,
        clause="п. 3.9",
        value=15.0,
        unit="м²",
        rationale="Зона для посетителей рассчитывается из 15–20 м² на каждое операционное место. "
        "Проверяется нижняя граница.",
    ),
    Rule(
        id="movement_path_width",
        title="Ширина пути движения посетителей",
        category=Category.ACCESSIBILITY,
        severity=Severity.ERROR,
        source=SP59,
        clause="п. 6.2.1",
        value=1.8,
        unit="м",
        rationale="Пути движения должны быть не менее 1,8 м. Допускается 1,5–1,2 м при наличии "
        "карманов для разъезда длиной не менее 2 м в пределах прямой видимости.",
    ),
    Rule(
        id="wheelchair_turning_circle",
        title="Зона разворота кресла-коляски",
        category=Category.ACCESSIBILITY,
        severity=Severity.ERROR,
        source=SP59,
        clause="п. 6.1.8",
        value=1.4,
        unit="м",
        rationale="Диаметр зоны самостоятельного разворота на 180° — не менее 1,4 м, "
        "зона не должна пересекаться с зоной открывания двери.",
    ),
    Rule(
        id="counter_height",
        title="Высота низа окна обслуживания",
        category=Category.ACCESSIBILITY,
        severity=Severity.ERROR,
        source=SP59,
        clause="п. 8.1.7",
        value=0.85,
        unit="м",
        rationale="Поверхность прилавка и низ окна кассы для посетителя на кресле-коляске — "
        "не более 0,80–0,85 м над уровнем пола.",
    ),
    Rule(
        id="counter_front_width",
        title="Ширина рабочего фронта стойки обслуживания",
        category=Category.ACCESSIBILITY,
        severity=Severity.WARNING,
        source=SP59,
        clause="п. 8.1.7",
        value=1.0,
        unit="м",
        rationale="Рабочий фронт прилавка или стойки у места получения услуги — не менее 1,0 м.",
    ),
    Rule(
        id="door_maneuver_space",
        title="Пространство для манёвра перед дверью",
        category=Category.ACCESSIBILITY,
        severity=Severity.WARNING,
        source=SP59,
        clause="п. 6.1.8",
        value=1.5,
        unit="м",
        rationale="При открывании двери «к себе» требуется площадка не менее 1,5 × 1,5 м.",
    ),
    Rule(
        id="queue_terminal_near_entrance",
        title="Размещение терминала электронной очереди",
        category=Category.TECHNOLOGY,
        severity=Severity.WARNING,
        source=EQUIPMENT_LIST,
        clause="раздел «Зона обслуживания клиентов»",
        value=3.0,
        unit="м",
        rationale="Терминал управления очередью размещается у входа, в радиусе не более 3 м.",
    ),
    Rule(
        id="ramp_width",
        title="Ширина пандуса при входе",
        category=Category.ACCESSIBILITY,
        severity=Severity.WARNING,
        source=EQUIPMENT_LIST,
        clause="раздел «Вспомогательные элементы»",
        value=1.2,
        unit="м",
        rationale="Пандус при входе — шириной от 1,2 м при уклоне до 1:12.",
    ),
    Rule(
        id="coverage_per_service_window",
        title="Охват населения на количество окон обслуживания",
        category=Category.CAPACITY,
        severity=Severity.INFO,
        source=ORDER_538,
        clause="пп. 2–4",
        value=None,
        unit="тыс. чел.",
        rationale="Расчётное количество обслуживаемого населения умножается на количество окон "
        "обслуживания: свыше 500 тыс. жителей — 15 тыс. на окно, 250–500 — 10–15, "
        "100–250 — 6–10, до 100 — до 6, сельские поселения — 5.",
    ),
    Rule(
        id="parcel_storage_capacity",
        title="Ёмкость хранения посылок",
        category=Category.CAPACITY,
        severity=Severity.ERROR,
        source=VNTP,
        clause="п. 3.10",
        unit="мест",
        rationale="Кладовая посылок к выдаче — на 200 % среднесуточного входящего объёма и 60 % "
        "исходящего. Проверяется, когда объёмы посылок заданы в паспорте отделения.",
    ),
    Rule(
        id="evacuation_path_width",
        title="Ширина пути эвакуации",
        category=Category.FIRE,
        severity=Severity.ERROR,
        source=SP1,
        clause="п. 4.3.3",
        value=1.0,
        unit="м",
        rationale="Из каждой зоны к выходу наружу нужен путь шириной в свету не менее 1,2 м, если "
        "эвакуироваться могут больше 50 человек, 0,7 м — к одиночным рабочим местам, 1,0 м — в "
        "остальных случаях. Места ожидания и оборудование на пути допустимы, но ширину нужно "
        "сохранить (ПП РФ № 1479, пп. 27 «б», 28).",
    ),
    Rule(
        id="vestibule_storage",
        title="Хранение в тамбуре выхода",
        category=Category.FIRE,
        severity=Severity.ERROR,
        source=PPR,
        clause="п. 27 «в»",
        rationale="В тамбурах выходов из здания запрещено хранить, в том числе временно, "
        "инвентарь и материалы: стеллажи и шкафы там стоять не должны.",
    ),
    Rule(
        id="fire_extinguishers",
        title="Огнетушители",
        category=Category.FIRE,
        severity=Severity.WARNING,
        source=PPR,
        clause="пп. 400, 406",
        value=20.0,
        unit="м",
        rationale="В общественном здании на этаже не менее 2 огнетушителей (п. 400); путь от любого "
        "места до огнетушителя с учётом перегородок и оборудования — не длиннее 20 м (п. 406).",
    ),
]

PARCEL_WORKPLACE_SQM = 7.5
CORRESPONDENCE_WORKPLACE_SQM = 6.0
MIN_PATH_WITH_POCKETS_M = 1.2

COVERAGE_PER_WINDOW: list[tuple[float, float, float]] = [
    # (нижняя граница населения пункта в тыс., охват на окно от, охват на окно до)
    (500.0, 15.0, 15.0),
    (250.0, 10.0, 15.0),
    (100.0, 6.0, 10.0),
    (0.0, 0.0, 6.0),
]
RURAL_COVERAGE_PER_WINDOW = 5.0


def rules_by_id() -> dict[str, Rule]:
    return {rule.id: rule for rule in DEFAULT_RULES}
