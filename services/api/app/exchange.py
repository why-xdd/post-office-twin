"""Обмен с учётной системой (1С): выгрузка описи оборудования и сверка плана с учётом.

План знает, где стоит каждый объект, учёт — что числится на балансе. Расхождения между ними и
есть то, что ищет инвентаризация: объект числится, но на плане его нет (недостача), стоит на
плане, но не числится (излишек), числится в одном месте, а стоит в другом.

Формат — таблица с разделителем «;», как её сохраняет Excel в русской локали. Выгрузку 1С
принимает через «Загрузку из табличного документа»; отчёт «Ведомость основных средств»,
сохранённый в CSV, читается обратно. Названия колонок распознаются по смыслу, а не по точному
написанию: у разных конфигураций 1С они немного различаются.
"""

from __future__ import annotations

import csv
import io
import re
from datetime import date

from pydantic import BaseModel

from app.dictionaries import equipment_types
from app.models import Equipment, Plan

EXPORT_COLUMNS = (
    "Инвентарный номер",
    "Наименование",
    "Серийный номер",
    "Изготовитель",
    "Модель",
    "Местонахождение",
    "Идентификатор на плане",
    "Дата последнего осмотра",
    "Дата следующего осмотра",
)

# Как колонка может называться в выгрузке из учёта. Сравнение — по нормализованному заголовку.
COLUMN_ALIASES = {
    "inventory_number": ("инвентарный номер", "инв номер", "инв №", "инвентарный №", "инв. номер"),
    "name": ("наименование", "основное средство", "объект", "ос"),
    "serial_number": ("серийный номер", "заводской номер", "зав номер", "зав №", "серийный №"),
    "location": ("местонахождение", "местонахождение ос", "место хранения", "подразделение", "зона"),
    "plan_id": ("идентификатор на плане", "идентификатор", "id"),
}


class AccountingRow(BaseModel):
    """Строка учёта: объект, который числится на балансе."""

    line: int
    inventory_number: str | None = None
    name: str | None = None
    serial_number: str | None = None
    location: str | None = None
    plan_id: str | None = None


class Match(BaseModel):
    object_id: str
    label: str
    inventory_number: str | None = None
    zone: str | None = None
    accounting_location: str | None = None
    location_differs: bool = False
    serial_differs: bool = False
    matched_by: str


class Surplus(BaseModel):
    """Стоит на плане, но в учёте не найден."""

    object_id: str
    label: str
    inventory_number: str | None = None
    serial_number: str | None = None
    zone: str | None = None


class Reconciliation(BaseModel):
    matched: list[Match]
    missing_on_plan: list[AccountingRow]
    not_in_accounting: list[Surplus]
    location_mismatches: int
    serial_mismatches: int
    rows_read: int
    columns_recognised: dict[str, str]
    summary: str


def _norm(text: str | None) -> str:
    return re.sub(r"[\s.]+", " ", (text or "").strip().lower().replace("ё", "е")).strip()


def _key(text: str | None) -> str:
    """Номер для сравнения: без пробелов, дефисов и регистра — «ЗР-ОК 0101» совпадёт с «зрок0101»."""
    return re.sub(r"[\s\-_/.]+", "", (text or "").lower())


def _zone_label(plan: Plan, item: Equipment) -> str | None:
    zone = next((z for z in plan.zones if z.id == item.zone), None)
    if zone is None:
        return None
    return zone.label or zone.type


def _label(plan: Plan, item: Equipment) -> str:
    if item.label:
        return item.label
    known = equipment_types(plan).get(item.type)
    return known.label if known else item.type


def export_csv(plan: Plan) -> bytes:
    """Опись оборудования плана для загрузки в учёт. UTF-8 с BOM, чтобы Excel не путал кодировку."""
    buffer = io.StringIO()
    writer = csv.writer(buffer, delimiter=";", quoting=csv.QUOTE_MINIMAL, lineterminator="\r\n")
    writer.writerow(EXPORT_COLUMNS)
    for item in plan.equipment:
        inventory = item.inventory
        writer.writerow(
            [
                (inventory.inventory_number if inventory else None) or "",
                _label(plan, item),
                (inventory.serial_number if inventory else None) or "",
                (inventory.vendor if inventory else None) or "",
                (inventory.model if inventory else None) or "",
                _zone_label(plan, item) or "",
                item.id,
                (inventory.last_inspection if inventory else None) or "",
                (inventory.next_inspection if inventory else None) or "",
            ]
        )
    return ("\ufeff" + buffer.getvalue()).encode("utf-8")


def decode(raw: bytes) -> str:
    """Текст файла из учёта: 1С и Excel сохраняют и в UTF-8, и в Windows-1251."""
    for encoding in ("utf-8-sig", "cp1251"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise ValueError("Не удалось прочитать файл: ожидается таблица в UTF-8 или Windows-1251")


def split_rows(text: str, is_header, delimiters: tuple[str, ...] = (";", "\t", ",")) -> tuple[list[list[str]], int, dict]:
    """Строки таблицы и её заголовок.

    Разделитель не угадывается по частоте символов: в русских таблицах запятая стоит и внутри
    значений («Стол, офисный»), и угадывание выбирает её. Вместо этого разделители пробуются по
    очереди, и берётся первый, при котором находится строка заголовков.
    """
    for delimiter in delimiters:
        rows = list(csv.reader(io.StringIO(text), delimiter=delimiter))
        # Отчёты 1С начинаются с шапки организации; таблица — с первой узнаваемой строки заголовков.
        for index, row in enumerate(rows[:30]):
            found = is_header(row)
            if found:
                return rows, index, found
    return [], -1, {}


def _accounting_header(row: list[str]) -> dict[str, int]:
    found: dict[str, int] = {}
    for position, cell in enumerate(row):
        name = _norm(cell)
        for field, aliases in COLUMN_ALIASES.items():
            if field not in found and name in {_norm(a) for a in aliases}:
                found[field] = position
    keys = found.keys()
    return found if {"inventory_number", "serial_number", "plan_id"} & keys else {}


def read_accounting(text: str) -> tuple[list[AccountingRow], dict[str, str]]:
    """Строки учёта и то, какие колонки удалось узнать."""
    rows, header_index, columns = split_rows(text, _accounting_header)
    if header_index < 0:
        raise ValueError(
            "В файле не нашлась колонка «Инвентарный номер», «Серийный номер» или «Идентификатор "
            "на плане» — сверять не по чему. Первая строка таблицы должна содержать заголовки."
        )
    recognised = {field: rows[header_index][position] for field, position in columns.items()}

    result: list[AccountingRow] = []
    for line, row in enumerate(rows[header_index + 1 :], start=header_index + 2):
        values = {field: (row[pos].strip() if pos < len(row) else "") for field, pos in columns.items()}
        if not (values.get("inventory_number") or values.get("serial_number") or values.get("plan_id")):
            continue  # пустые строки и итоги отчёта
        result.append(
            AccountingRow(
                line=line,
                inventory_number=values.get("inventory_number") or None,
                name=values.get("name") or None,
                serial_number=values.get("serial_number") or None,
                location=values.get("location") or None,
                plan_id=values.get("plan_id") or None,
            )
        )
    return result, recognised


def reconcile(plan: Plan, accounting: list[AccountingRow], recognised: dict[str, str] | None = None) -> Reconciliation:
    """Сверка: что совпало, чего нет на плане, чего нет в учёте, что стоит не там."""
    # Номер может повторяться на плане по ошибке: объекты с одним номером сопоставляются по
    # очереди, чтобы второй не оказался «не числится» при номере, который в учёте есть.
    by_inventory: dict[str, list[Equipment]] = {}
    by_serial: dict[str, list[Equipment]] = {}
    by_id: dict[str, Equipment] = {item.id: item for item in plan.equipment}
    for item in plan.equipment:
        if item.inventory and item.inventory.inventory_number:
            by_inventory.setdefault(_key(item.inventory.inventory_number), []).append(item)
        if item.inventory and item.inventory.serial_number:
            by_serial.setdefault(_key(item.inventory.serial_number), []).append(item)

    used: set[str] = set()

    def first_free(candidates: list[Equipment] | None) -> Equipment | None:
        return next((c for c in candidates or [] if c.id not in used), None)

    matched: list[Match] = []
    missing: list[AccountingRow] = []
    for row in accounting:
        item, how = None, ""
        if row.inventory_number and (found := first_free(by_inventory.get(_key(row.inventory_number)))):
            item, how = found, "инвентарный номер"
        elif row.serial_number and (found := first_free(by_serial.get(_key(row.serial_number)))):
            item, how = found, "серийный номер"
        elif row.plan_id and row.plan_id in by_id and row.plan_id not in used:
            item, how = by_id[row.plan_id], "идентификатор на плане"
        if item is None:
            missing.append(row)
            continue
        used.add(item.id)
        zone = _zone_label(plan, item)
        location_differs = bool(row.location and zone and _norm(row.location) != _norm(zone))
        plan_serial = item.inventory.serial_number if item.inventory else None
        serial_differs = bool(row.serial_number and plan_serial and _key(row.serial_number) != _key(plan_serial))
        matched.append(
            Match(
                object_id=item.id,
                label=_label(plan, item),
                inventory_number=row.inventory_number,
                zone=zone,
                accounting_location=row.location,
                location_differs=location_differs,
                serial_differs=serial_differs,
                matched_by=how,
            )
        )

    # В излишки попадает только то, что учитывается поштучно: у чего есть паспорт с номером.
    # Стулья и урны без номеров на балансе как отдельные объекты не числятся.
    surplus = [
        Surplus(
            object_id=item.id,
            label=_label(plan, item),
            inventory_number=item.inventory.inventory_number if item.inventory else None,
            serial_number=item.inventory.serial_number if item.inventory else None,
            zone=_zone_label(plan, item),
        )
        for item in plan.equipment
        if item.id not in used and item.inventory and (item.inventory.inventory_number or item.inventory.serial_number)
    ]
    moved = sum(m.location_differs for m in matched)
    serial = sum(m.serial_differs for m in matched)
    parts = [f"Сверено строк учёта: {len(accounting)}, совпало {len(matched)}"]
    if missing:
        parts.append(f"нет на плане — {len(missing)}")
    if surplus:
        parts.append(f"нет в учёте — {len(surplus)}")
    if moved:
        parts.append(f"не на своём месте — {moved}")
    if serial:
        parts.append(f"расходится серийный номер — {serial}")
    if not (missing or surplus or moved or serial):
        parts.append("расхождений нет")
    return Reconciliation(
        matched=matched,
        missing_on_plan=missing,
        not_in_accounting=surplus,
        location_mismatches=moved,
        serial_mismatches=serial,
        rows_read=len(accounting),
        columns_recognised=recognised or {},
        summary=", ".join(parts) + ".",
    )


def export_filename(plan: Plan) -> str:
    name = re.sub(r'[\\/:*?"<>|]+', " ", plan.ops.name or "план").strip() or "план"
    return f"{name} — опись для 1С {date.today().isoformat()}.csv"
