"""Команды словами.

Пользователь пишет «поставь две кассы у перегородки и терминал очереди у входа» — сервис
разбирает фразу в те же действия, что делает панель ассистента: найти место, переставить,
убрать, пересчитать. Геометрию и нормы по-прежнему считает расчёт, а не язык: разбор только
выбирает, что именно посчитать.

Разбор словарный: команды в этой области короткие и однотипные, и словаря синонимов по
каждому типу оборудования и зоне хватает. Опечатки прощаются сравнением по близости слов —
человек печатает быстро и не всегда попадает по клавишам. Что не разобралось, возвращается
списком: сервис не угадывает, а переспрашивает.

Вопрос — не команда. «Сколько у нас окон?» и «где стоит банкомат?» раньше понимались как
«поставить окно» и «поставить банкомат»; теперь на вопрос сервис отвечает по плану, ничего в
нём не меняя.
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
    "cashier_window": ("касс", "окно обслуживания", "операционн", "окошк"),
    "pvz_counter": ("пвз", "выдач", "стойка выдачи"),
    "self_service_kiosk": ("терминал очеред", "электронн очеред", "киоск", "самообслуж", "талон"),
    "payment_terminal_stand": ("терминал оплат", "оплат"),
    "package_drop_box": ("почтовый ящик", "ящик для", "корреспонденц", "абонентский ящик"),
    "info_display": ("табло", "информационное табло"),
    "media_panel": ("медиапанел", "экран"),
    "waiting_seat": ("скам", "лавк", "кресл", "сиден", "стул", "место для ожидан"),
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
    "sorting_table": ("стол сортировк", "сортировочн"),
}

ZONE_SYNONYMS: dict[str, tuple[str, ...]] = {
    "client_area": ("клиентск", "зал", "для посетител"),
    "waiting": ("ожидан",),
    "queue_accumulator": ("накопител", "очеред"),
    "vestibule": ("тамбур",),
    "back_office": ("служебн", "складск", "подсобк"),
    "staff_room": ("персонал", "комнат"),
}

ADD_WORDS = ("поставь", "поставим", "постав", "добавь", "добавьте", "размести", "установи", "нужно", "нужен", "нужна", "хочу")
REMOVE_WORDS = ("убери", "удали", "убрать", "снеси", "демонтируй", "убрат")
MOVE_WORDS = ("передвинь", "перенеси", "переставь", "сдвинь", "подвинь", "перемести")
# Всё, что означает «разберись сам»: устранение нарушений и расширение проходов.
FIX_WORDS = (
    "исправь", "почини", "расставь", "оптимизируй", "в порядок", "по нормам",
    "расширь", "шире", "разберись", "исправить",
)
CHECK_WORDS = ("проверь", "что не так", "какие нарушения", "проверка", "всё ли в порядке")

# Вопросительные слова: с них начинается не команда, а вопрос о плане.
COUNT_QUESTIONS = ("сколько",)
WHERE_QUESTIONS = ("где", "куда поставили", "в какой зоне", "покажи", "найди", "подсвети", "выдели")
AREA_QUESTIONS = ("какая площадь", "сколько площади", "свободная площадь", "сколько места")
STATE_QUESTIONS = ("что с планом", "как дела с планом", "всё ли хорошо")

NEAR_ENTRANCE_WORDS = ("у вход", "около вход", "возле вход", "рядом с вход", "при вход", "ко вход")
NEAR_WALL_WORDS = ("у стен", "к стене", "вдоль стен", "рядом со стен", "около стен", "пристен")
NEAR_PARTITION_WORDS = ("перегородк",)

# Единицы измерения: число перед ними — размер или расстояние, а не количество предметов.
UNITS = ("м", "мм", "см", "метр", "метра", "метров", "метрах", "кв", "%", "процент")


@dataclass
class Action:
    """Одно понятое действие: что сделать, с чем и где."""

    kind: str  # add | move | remove | fix | check | question
    equipment_type: str | None = None
    object_id: str | None = None
    zone_id: str | None = None
    count: int = 1
    near_entrance: bool = False
    near_partition: bool = False
    near_wall: bool = False
    # Для вопроса: о чём спросили — count | where | area | state.
    question: str | None = None
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


@dataclass(frozen=True)
class _Match:
    """Найденный во фразе тип оборудования и место, которым он опознан."""

    code: str
    start: int
    end: int


def _normalise(text: str) -> str:
    return re.sub(r"\s+", " ", text.lower().replace("ё", "е")).strip()


ZONE_HINTS = tuple(hint for hints in ZONE_SYNONYMS.values() for hint in hints)


def _belongs_to_zone(word: str) -> bool:
    """Слово называет зону, а не предмет.

    «Ожидание» есть и в названии скамьи, и в названии зоны ожидания. Если оставить его приметой
    скамьи, то «сколько скамеек в зоне ожидания» опознаётся по слову «ожидания» — и зона из
    фразы исчезает. Такие слова остаются за зонами: у предметов есть свои названия.
    """
    return any(word.startswith(hint) or hint.startswith(word) for hint in ZONE_HINTS)


def _distance(first: str, second: str, limit: int) -> int:
    """Число правок, которыми одно слово превращается в другое. Больше limit не считается."""
    if abs(len(first) - len(second)) > limit:
        return limit + 1
    previous = list(range(len(second) + 1))
    for index, letter in enumerate(first, start=1):
        current = [index]
        for position, other in enumerate(second, start=1):
            current.append(
                min(
                    previous[position] + 1,
                    current[position - 1] + 1,
                    previous[position - 1] + (letter != other),
                )
            )
        if min(current) > limit:
            return limit + 1
        previous = current
    return previous[-1]


def _full_words(plan: Plan) -> dict[str, str]:
    """Полные слова названий и синонимов: по ним прощаются опечатки.

    Сравнение идёт с целым словом, а не с его началом: пропущенная буква внутри слова сдвигает
    всё остальное, и от обрезанной приметы «термин» слово «термнал» отличается уже двумя
    правками, а от целого «терминал» — одной.
    """
    known = equipment_types(plan)
    words: dict[str, str] = {}
    for code, definition in known.items():
        for word in re.split(r"[ /(),]+", _normalise(definition.label)):
            if len(word) >= 6 and not _belongs_to_zone(word[:6]):
                words.setdefault(word, code)
    for code, hints in SYNONYMS.items():
        if code not in known:
            continue
        for hint in hints:
            hint = _normalise(hint)
            if len(hint) >= 6 and " " not in hint:
                words.setdefault(hint, code)
    return words


def _stems(plan: Plan) -> dict[str, list[str]]:
    """Слова-приметы по каждому типу оборудования: из названия и из словаря синонимов."""
    known = equipment_types(plan)
    result: dict[str, list[str]] = {}
    for code, definition in known.items():
        words = [
            word[:6]
            for word in re.split(r"[ /(),]+", _normalise(definition.label))
            if len(word) >= 6 and not _belongs_to_zone(word[:6])
        ]
        result[code] = words
    for code, hints in SYNONYMS.items():
        if code in known:
            result.setdefault(code, []).extend(_normalise(hint) for hint in hints)
    return result


def _find_type(fragment: str, plan: Plan) -> _Match | None:
    """Тип оборудования по куску фразы и место фразы, которым он опознан.

    Берётся самое раннее совпадение: предмет называют сразу после глагола, а зона идёт дальше,
    после предлога. Поэтому «скамью в накопитель очереди» — это скамья, а не терминал очереди.
    Если ничего не нашлось, слова фразы сравниваются с приметами по близости: «термнал очереди»
    должен пониматься так же, как «терминал очереди».
    """
    stems = _stems(plan)
    matches: list[tuple[int, int, str]] = []  # позиция, длина, код
    for code, words in stems.items():
        for stem in words:
            position = fragment.find(stem)
            if position >= 0:
                matches.append((position, len(stem), code))
    if matches:
        matches.sort(key=lambda m: (m[0], -m[1]))
        position, length, code = matches[0]
        return _Match(code=code, start=position, end=position + length)
    return _fuzzy_type(fragment, plan)


def _fuzzy_type(fragment: str, plan: Plan) -> _Match | None:
    """Тип по слову с опечаткой.

    Короткие слова не сравниваются: в них любая ошибка меняет смысл («стенд» и «стена»
    отличаются одной буквой, но это разные вещи). Длинному слову прощаются две правки, слову
    средней длины — одна.
    """
    words = _full_words(plan)
    for found in re.finditer(r"[а-яa-z]{6,}", fragment):
        word = found.group()
        best: tuple[int, str] | None = None
        for known, code in words.items():
            limit = 2 if max(len(word), len(known)) >= 9 else 1
            distance = _distance(word, known, limit)
            if distance <= limit and (best is None or distance < best[0]):
                best = (distance, code)
        if best is not None:
            return _Match(code=best[1], start=found.start(), end=found.end())
    return None


def _without(fragment: str, match: _Match | None) -> str:
    """Фраза без того куска, которым опознан тип: по остатку ищется зона."""
    if match is None:
        return fragment
    return fragment[: match.start] + " " + fragment[match.end :]


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


ADDRESS = re.compile(r"\bо[\s-]?(\d{1,2})\b")


def _find_object(fragment: str, plan: Plan) -> str | None:
    """Объект по адресу (О-03) или по подписи."""
    address = ADDRESS.search(fragment)
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
    """Сколько предметов просят.

    Из фразы сначала убираются числа, которые количеством не являются: адрес объекта («О-12»)
    и размеры с единицами измерения («в 2 метрах от стены»). Иначе сервис ставил двенадцать
    почтовых ящиков там, где просили один.
    """
    clean = ADDRESS.sub(" ", fragment)
    clean = re.sub(r"\b\d{1,3}[\s-]?(?:%s)\b" % "|".join(UNITS), " ", clean)
    digits = re.search(r"\b(\d{1,2})\b", clean)
    if digits:
        return max(1, min(20, int(digits.group(1))))
    for word, value in NUMBERS.items():
        if re.search(rf"\b{word}\b", clean):
            return value
    return 1


def _question(part: str) -> str | None:
    """О чём спрашивают, если это вопрос, а не команда."""
    if any(hint in part for hint in AREA_QUESTIONS):
        return "area"
    if any(part.startswith(hint) or f" {hint} " in part for hint in COUNT_QUESTIONS):
        return "count"
    if any(part.startswith(hint) or f" {hint} " in part for hint in WHERE_QUESTIONS):
        return "where"
    if any(hint in part for hint in STATE_QUESTIONS):
        return "state"
    return None


def understand(text: str, plan: Plan) -> Understanding:
    """Разбирает фразу в набор действий. Непонятые куски возвращаются отдельно."""
    result = Understanding()
    normalised = _normalise(text)
    if not normalised:
        return result

    # Фраза делится на части: «поставь кассу у стены и убери банкомат» — это два действия.
    parts = [
        p.strip(" ?!")
        for p in re.split(
            r"[.;?!]|\bи\b|,(?=\s*(?:поставь|добавь|убери|удали|перенеси|переставь))", normalised
        )
        if p.strip(" ?!")
    ]
    for part in parts:
        action = _understand_part(part, plan)
        if action is None:
            result.unknown.append(part)
        else:
            result.actions.append(action)
    return result


def _understand_part(part: str, plan: Plan) -> Action | None:
    near_entrance = any(hint in part for hint in NEAR_ENTRANCE_WORDS)
    near_partition = any(hint in part for hint in NEAR_PARTITION_WORDS)
    near_wall = any(hint in part for hint in NEAR_WALL_WORDS)
    found = _find_type(part, plan)
    type_code = found.code if found else None
    # Зона ищется во фразе без той части, которой опознан тип оборудования: иначе «скамью в
    # зону ожидания» теряет зону, потому что «ожидание» есть и в названии скамьи.
    zone_id = _find_zone(_without(part, found), plan)
    object_id = _find_object(part, plan)

    # Вопрос разбирается раньше команд: «сколько у нас окон» — это не «поставить окно».
    asked = _question(part)
    if asked is not None and not any(word in part for word in ADD_WORDS + REMOVE_WORDS + MOVE_WORDS):
        return Action(
            kind="question",
            question=asked,
            equipment_type=type_code,
            object_id=object_id,
            zone_id=zone_id,
            text=part,
        )
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
