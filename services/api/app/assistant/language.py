"""Команды словами.

Пользователь пишет «поставь две кассы у перегородки и терминал очереди у входа» — сервис
разбирает фразу в те же действия, что делает панель ассистента: найти место, переставить,
убрать, пересчитать. Геометрию и нормы по-прежнему считает расчёт, а не язык: разбор только
выбирает, что именно посчитать.

Разбор словарный: команды в этой области короткие и однотипные, и словаря синонимов по
каждому типу оборудования и зоне хватает. Что не разобралось, возвращается списком — сервис
не угадывает, а переспрашивает.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from app.dictionaries import equipment_types, zone_types
from app.models import Plan

NUMBERS = {
    "один": 1, "одну": 1, "одно": 1, "два": 2, "две": 2, "три": 3, "четыре": 4, "пять": 5,
    "шесть": 6, "семь": 7, "восемь": 8, "девять": 9, "десять": 10, "пару": 2, "несколько": 2,
}

# Слова, по которым узнаётся тип оборудования, когда полное название никто не пишет.
SYNONYMS: dict[str, tuple[str, ...]] = {
    "cashier_window": ("касс", "окно обслуживания", "операционн"),
    "pvz_counter": ("пвз", "выдач", "стойка выдачи"),
    "self_service_kiosk": ("терминал очеред", "электронн очеред", "киоск", "самообслуж", "талон"),
    "payment_terminal_stand": ("терминал оплат", "оплат"),
    "package_drop_box": ("почтовый ящик", "ящик для", "корреспонденц"),
    "info_display": ("табло", "информационное табло"),
    "media_panel": ("медиапанел", "экран"),
    "waiting_seat": ("скамь", "кресл", "сиден", "стул"),
    "atm": ("банкомат",),
    "parcel_locker": ("почтомат", "постамат"),
    "back_office_workplace": ("рабочее место", "арм", "стол сотрудник"),
    "printer": ("принтер", "мфу"),
    "label_printer": ("принтер для этикеток", "этикет"),
    "camera": ("камер", "видеонаблюден"),
    "fire_alarm": ("пожарн", "извещател"),
    "security_alarm": ("охранн", "тревожн"),
    "storage_rack": ("стеллаж", "шкаф"),
    "goods_rack": ("стеллаж для товаров", "витрин"),
    "weighing_station": ("вес",),
    "info_stand": ("стенд", "навигац"),
    "safe": ("сейф",),
    "fitting_room": ("примерочн",),
    "trash_bin": ("урн", "мусор"),
    "potted_plant": ("растен", "цвет"),
}

ZONE_SYNONYMS: dict[str, tuple[str, ...]] = {
    "client_area": ("клиентск", "зал", "для посетител"),
    "waiting": ("ожидан",),
    "queue_accumulator": ("накопител", "очеред"),
    "vestibule": ("тамбур",),
    "back_office": ("служебн", "складск", "подсобк"),
    "staff_room": ("персонал", "комнат"),
}

ADD_WORDS = ("поставь", "добавь", "размести", "постав", "нужно поставить", "установи", "хочу")
REMOVE_WORDS = ("убери", "удали", "убрать", "снеси", "демонтируй")
MOVE_WORDS = ("передвинь", "перенеси", "переставь", "сдвинь", "подвинь")
FIX_WORDS = ("исправь", "почини", "расставь", "оптимизируй", "приведи в порядок", "сделай по нормам")
CHECK_WORDS = ("проверь", "что не так", "какие нарушения", "проверка")


@dataclass
class Action:
    """Одно понятое действие: что сделать, с чем и где."""

    kind: str  # add | move | remove | fix | check
    equipment_type: str | None = None
    object_id: str | None = None
    zone_id: str | None = None
    count: int = 1
    near_entrance: bool = False
    near_partition: bool = False
    near_wall: bool = False
    text: str = ""


@dataclass
class Understanding:
    """Что сервис понял из фразы и о чём не догадался."""

    actions: list[Action] = field(default_factory=list)
    unknown: list[str] = field(default_factory=list)
    source: str = "разбор фразы"

    @property
    def understood(self) -> bool:
        return bool(self.actions)


def _normalise(text: str) -> str:
    return re.sub(r"\s+", " ", text.lower().replace("ё", "е")).strip()


def _find_type(fragment: str, plan: Plan) -> tuple[str, str] | None:
    """Тип оборудования по куску фразы и то место фразы, которым он опознан.

    Берётся самое раннее совпадение: предмет называют сразу после глагола, а зона идёт дальше,
    после предлога. Поэтому «скамью в накопитель очереди» — это скамья, а не терминал очереди.
    Возвращается и сам кусок фразы: по остатку потом ищется зона.
    """
    known = equipment_types(plan)
    matches: list[tuple[int, int, str, str]] = []  # позиция, длина, код, кусок фразы
    for code, definition in known.items():
        for word in re.split(r"[ /(),]+", _normalise(definition.label)):
            if len(word) < 6:
                continue
            stem = word[:6]
            position = fragment.find(stem)
            if position >= 0:
                matches.append((position, len(stem), code, stem))
    for code, hints in SYNONYMS.items():
        if code not in known:
            continue
        for hint in hints:
            stem = _normalise(hint)
            position = fragment.find(stem)
            if position >= 0:
                matches.append((position, len(stem), code, stem))
    if not matches:
        return None
    matches.sort(key=lambda m: (m[0], -m[1]))
    return matches[0][2], matches[0][3]


def _find_zone(fragment: str, plan: Plan) -> str | None:
    """Зона плана по куску фразы: по названию самой зоны или по типу зоны."""
    for zone in plan.zones:
        label = _normalise(zone.label or "")
        if label and label[:10] in fragment:
            return zone.id
    types = zone_types(plan)
    for code, hints in ZONE_SYNONYMS.items():
        if code not in types:
            continue
        if any(hint in fragment for hint in hints):
            match = next((z for z in plan.zones if z.type == code), None)
            if match:
                return match.id
    return None


def _find_object(fragment: str, plan: Plan) -> str | None:
    """Объект по адресу (О-03) или по подписи."""
    address = re.search(r"\bо[\s-]?(\d{1,2})\b", fragment)
    if address:
        index = int(address.group(1)) - 1
        if 0 <= index < len(plan.equipment):
            return plan.equipment[index].id
    for item in plan.equipment:
        label = _normalise(item.label or "")
        if label and len(label) > 4 and label[:8] in fragment:
            return item.id
    return None


def _count(fragment: str) -> int:
    digits = re.search(r"\b(\d{1,2})\b", fragment)
    if digits:
        return max(1, min(20, int(digits.group(1))))
    for word, value in NUMBERS.items():
        if word in fragment:
            return value
    return 1


def understand(text: str, plan: Plan) -> Understanding:
    """Разбирает фразу в набор действий. Непонятые куски возвращаются отдельно."""
    result = Understanding()
    normalised = _normalise(text)
    if not normalised:
        return result

    # Фраза делится на части: «поставь кассу у стены и убери банкомат» — это два действия.
    parts = [p.strip() for p in re.split(r"[.;]|\bи\b|,(?=\s*(?:поставь|добавь|убери|удали|перенеси|переставь))", normalised) if p.strip()]
    for part in parts:
        action = _understand_part(part, plan)
        if action is None:
            result.unknown.append(part)
        else:
            result.actions.append(action)
    return result


def _understand_part(part: str, plan: Plan) -> Action | None:
    near_entrance = "у вход" in part or "около вход" in part or "возле вход" in part
    near_partition = "перегородк" in part
    near_wall = "у стен" in part or "к стене" in part or "вдоль стен" in part
    found_type = _find_type(part, plan)
    type_code = found_type[0] if found_type else None
    # Зона ищется во фразе без той части, которой опознан тип оборудования.
    rest = part.replace(found_type[1], " ") if found_type else part
    zone_id = _find_zone(rest, plan)
    object_id = _find_object(part, plan)

    if any(word in part for word in FIX_WORDS):
        return Action(kind="fix", text=part)
    if any(word in part for word in CHECK_WORDS):
        return Action(kind="check", text=part)
    if any(word in part for word in REMOVE_WORDS):
        if object_id or type_code:
            return Action(kind="remove", object_id=object_id, equipment_type=type_code, text=part)
        return None
    if any(word in part for word in MOVE_WORDS):
        if object_id or type_code:
            return Action(
                kind="move",
                object_id=object_id,
                equipment_type=type_code,
                zone_id=zone_id,
                near_entrance=near_entrance,
                near_partition=near_partition,
                near_wall=near_wall,
                text=part,
            )
        return None
    if any(word in part for word in ADD_WORDS) or type_code:
        if not type_code:
            return None
        return Action(
            kind="add",
            equipment_type=type_code,
            zone_id=zone_id,
            count=_count(part),
            near_entrance=near_entrance,
            near_partition=near_partition,
            near_wall=near_wall,
            text=part,
        )
    return None
