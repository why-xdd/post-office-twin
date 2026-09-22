"""Очередь по фактическим данным: журнал электронной очереди или почасовой счётчик посетителей.

Расчёт пропускной способности в `capacity.py` опирается на среднесуточный поток и допущение о
часе пик. Здесь то же самое считается по часам из реальных данных отделения:

- журнал электронной очереди (талон: выдан, вызван, обслуживание завершено, окно, услуга) даёт
  и поток, и фактическое ожидание, и фактическое время обслуживания;
- счётчик посетителей на входе (в том числе камера с подсчётом людей) даёт только поток по часам,
  ожидание тогда считается моделью M/M/c.

Для каждого часа сравнивается фактическое ожидание с тем, что даёт модель при окнах с плана, и
считается, сколько окон нужно, чтобы среднее ожидание не превышало 5 минут. Из этого получается
расписание: в какие часы окон не хватает, а в какие можно открыть меньше.
"""

from __future__ import annotations

import math
import re
from collections import defaultdict
from datetime import datetime, timedelta

from pydantic import BaseModel, Field

from app.assistant import capacity
from app.exchange import split_rows
from app.geometry import core
from app.models import Plan

MAX_WAIT_MIN = capacity.MAX_ACCEPTABLE_WAIT_MIN

COLUMN_ALIASES = {
    "issued": ("выдан", "время выдачи", "талон выдан", "выдача талона", "создан"),
    "called": ("вызван", "время вызова", "вызов", "начало обслуживания"),
    "finished": ("завершён", "завершен", "окончание обслуживания", "обслужен", "конец"),
    "window": ("окно", "рабочее место", "оператор"),
    "service": ("услуга", "тип услуги", "операция"),
    "date": ("дата", "день"),
    "hour": ("час", "время", "интервал"),
    "count": ("посетителей", "вошло", "входов", "количество", "посетители"),
}

# День записи, если в файле нет дат: все такие записи считаются одним днём.
UNDATED = "без даты"

# Какие услуги относятся к выдаче посылок; остальное — окна обслуживания.
PARCEL_WORDS = ("посыл", "выдач", "получени", "пвз")


class HourStat(BaseModel):
    hour: int
    arrivals_per_hour: float = Field(description="Средний поток за этот час по всем дням данных")
    measured_wait_min: float | None = None
    measured_wait_p90_min: float | None = None
    measured_service_min: float | None = None
    windows_open: float | None = Field(default=None, description="Сколько окон фактически работало")
    model_wait_min: float | None = Field(default=None, description="Ожидание по модели при окнах с плана")
    stable: bool = True
    windows_needed: int
    short: bool = Field(description="Окон на плане меньше, чем нужно в этот час")


class GroupHours(BaseModel):
    name: str
    windows_on_plan: int
    service_min: float
    service_source: str
    hours: list[HourStat]
    share_waited_over_limit: float | None = Field(
        default=None, description="Доля посетителей, ждавших дольше 5 минут (только по журналу)"
    )
    staff_hours_needed: int = Field(description="Окно-часов в день по расчёту")
    staff_hours_open: float | None = Field(default=None, description="Окно-часов в день по журналу")


class QueueLogReport(BaseModel):
    source: str
    days: int
    records: int
    skipped_rows: int
    groups: list[GroupHours]
    peak_hours: list[str]
    declared_peak_hours: list[str]
    summary: list[str]
    assumptions: list[str]


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", text.strip().lower().replace("ё", "е"))


def _columns(header: list[str]) -> dict[str, int]:
    """Колонки строки заголовков; пусто, если строка не похожа ни на журнал, ни на счётчик."""
    found: dict[str, int] = {}
    for position, cell in enumerate(header):
        name = _norm(cell)
        for field, aliases in COLUMN_ALIASES.items():
            if field not in found and name in {_norm(a) for a in aliases}:
                found[field] = position
    if {"issued", "called"} <= found.keys() or {"hour", "count"} <= found.keys():
        return found
    return {}


_DATETIME_FORMATS = ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%d.%m.%Y %H:%M:%S", "%d.%m.%Y %H:%M", "%Y-%m-%dT%H:%M:%S")
_TIME_FORMATS = ("%H:%M:%S", "%H:%M")


def _moment(value: str, day: str | None) -> datetime | None:
    value = value.strip()
    if not value:
        return None
    for fmt in _DATETIME_FORMATS:
        try:
            return datetime.strptime(value, fmt)
        except ValueError:
            continue
    for fmt in _TIME_FORMATS:
        try:
            clock = datetime.strptime(value, fmt)
        except ValueError:
            continue
        base = _day(day) if day else None
        base = base or datetime(2000, 1, 1)
        return base.replace(hour=clock.hour, minute=clock.minute, second=clock.second)
    return None


def _day(value: str) -> datetime | None:
    for fmt in ("%Y-%m-%d", "%d.%m.%Y"):
        try:
            return datetime.strptime(value.strip(), fmt)
        except ValueError:
            continue
    return None


def _hour_of(value: str) -> int | None:
    match = re.match(r"\s*(\d{1,2})(?::\d{2})?", value)
    if not match:
        return None
    hour = int(match.group(1))
    return hour if 0 <= hour <= 23 else None


def _group_of(service: str | None) -> str:
    text = _norm(service or "")
    return "Выдача посылок" if any(word in text for word in PARCEL_WORDS) else "Окна обслуживания"


def read(text: str) -> tuple[str, list[dict], int]:
    """Строки файла: вид данных, записи и число пропущенных строк."""
    rows, header_index, columns = split_rows(text, _columns)
    if header_index < 0:
        raise ValueError(
            "Не удалось узнать формат. Нужен журнал электронной очереди с колонками «Выдан» и "
            "«Вызван» (можно «Завершён», «Окно», «Услуга») или счётчик с колонками «Час» и «Посетителей»."
        )

    def cell(row: list[str], field: str) -> str | None:
        position = columns.get(field)
        return row[position].strip() if position is not None and position < len(row) else None

    records: list[dict] = []
    skipped = 0
    ticket_log = {"issued", "called"} <= columns.keys()
    for row in rows[header_index + 1 :]:
        if not any(c.strip() for c in row):
            continue
        day = cell(row, "date")
        if ticket_log:
            raw_issued = cell(row, "issued") or ""
            dated = bool(day and _day(day)) or bool(re.search(r"\d[-.]\d", raw_issued))
            issued = _moment(raw_issued, day)
            called = _moment(cell(row, "called") or "", day)
            finished = _moment(cell(row, "finished") or "", day)
            # Время без даты: вызов «00:03» после талона «23:58» — это уже следующие сутки.
            if issued is not None and called is not None and called < issued and called.date() == issued.date():
                called += timedelta(days=1)
            if called is not None and finished is not None and finished < called:
                finished += timedelta(days=1)
            if issued is None or called is None or called < issued:
                skipped += 1
                continue
            records.append(
                {
                    "day": issued.date().isoformat() if dated else UNDATED,
                    "hour": issued.hour,
                    "wait": (called - issued).total_seconds() / 60.0,
                    "service": (finished - called).total_seconds() / 60.0
                    if finished is not None and finished >= called
                    else None,
                    "window": cell(row, "window") or None,
                    "call_hour": called.hour,
                    "group": _group_of(cell(row, "service")),
                }
            )
        else:
            hour = _hour_of(cell(row, "hour") or "")
            try:
                # Excel в русской локали разделяет тысячи неразрывным пробелом: «1 234».
                count = float(re.sub(r"\s", "", cell(row, "count") or "").replace(",", "."))
            except ValueError:
                count = -1
            if hour is None or count < 0:
                skipped += 1
                continue
            records.append({"day": day if day and _day(day) else UNDATED, "hour": hour, "count": count})
    if not records:
        raise ValueError("В файле нет ни одной строки, которую можно разобрать")
    return ("журнал электронной очереди" if ticket_log else "счётчик посетителей"), records, skipped


def _windows(count: int) -> str:
    if count % 10 == 1 and count % 100 != 11:
        return f"{count} окно"
    if 2 <= count % 10 <= 4 and not 12 <= count % 100 <= 14:
        return f"{count} окна"
    return f"{count} окон"


def _percentile(values: list[float], share: float) -> float:
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, math.ceil(share * len(ordered)) - 1))
    return ordered[index]


def _peaks(totals: dict[int, float]) -> list[str]:
    """Два самых загруженных двухчасовых интервала, не пересекающихся между собой."""
    windows = sorted(
        ((totals.get(h, 0.0) + totals.get(h + 1, 0.0), h) for h in totals),
        reverse=True,
    )
    chosen: list[int] = []
    for _, start in windows:
        if all(abs(start - other) >= 2 for other in chosen):
            chosen.append(start)
        if len(chosen) == 2:
            break
    return [f"{h:02d}:00-{min(h + 2, 24):02d}:00" for h in sorted(chosen)]


def analyze(plan: Plan, source: str, records: list[dict], skipped: int = 0) -> QueueLogReport:
    days = sorted({r["day"] for r in records})
    day_count = max(len(days), 1)
    assumptions = [
        f"Поток за час усреднён по {day_count} {'дню' if day_count == 1 else 'дням'} данных.",
        "Модель M/M/c: посетители приходят независимо, обслуживание случайной длительности, "
        f"допустимым считается среднее ожидание не более {MAX_WAIT_MIN:g} мин.",
    ]
    if UNDATED in days:
        assumptions.append("В файле нет дат: записи без даты считаются одним днём.")
    by_group: dict[str, list[dict]] = defaultdict(list)
    if source == "счётчик посетителей":
        # Счётчик на входе не знает, к какому окну идёт посетитель: поток делится по долям
        # маршрутов из плана, как в расчёте пропускной способности.
        shares = capacity._path_shares(plan)
        split = {
            name: sum(shares.get(t, 0.0) for t in types) if shares else (1.0 if name == "Окна обслуживания" else 0.0)
            for name, types in capacity.GROUPS.items()
        }
        assumptions.append(
            "Счётчик считает всех вошедших; поток к окнам и к выдаче посылок взят по долям маршрутов "
            "плана: " + ", ".join(f"{name.lower()} {share:.0%}" for name, share in split.items()) + "."
        )
        for record in records:
            for name, share in split.items():
                if share > 0:
                    by_group[name].append({**record, "count": record["count"] * share})
    else:
        for record in records:
            by_group[record["group"]].append(record)

    groups: list[GroupHours] = []
    totals: dict[int, float] = defaultdict(float)
    for name, types in capacity.GROUPS.items():
        items = by_group.get(name)
        if not items:
            continue
        windows = len(core.equipment_by_type(plan, *types))
        served = [r["service"] for r in items if r.get("service") is not None and r["service"] > 0]
        if served:
            service = sum(served) / len(served)
            service_source = f"по журналу, {len(served)} обслуживаний"
        else:
            notes: list[str] = []
            service = capacity._service_minutes(plan, types, notes)
            if any(
                item.staff is not None and item.staff.avg_service_time_min
                for item in core.equipment_by_type(plan, *types)
            ):
                service_source = "по карточкам сотрудников"
            elif plan.ops.service_time_min:
                service_source = "по паспорту отделения"
            else:
                service_source = f"допущение {service:g} мин"
            assumptions.extend(notes)

        per_hour: dict[int, list[dict]] = defaultdict(list)
        for record in items:
            per_hour[record["hour"]].append(record)
        hours: list[HourStat] = []
        needed_total = 0
        open_total = 0.0
        has_windows = any(r.get("window") for r in items)
        for hour in sorted(per_hour):
            bucket = per_hour[hour]
            if source == "счётчик посетителей":
                arrivals = sum(r["count"] for r in bucket) / day_count
            else:
                arrivals = len(bucket) / day_count
            totals[hour] += arrivals
            waits = [r["wait"] for r in bucket if "wait" in r]
            model = capacity._queue(windows, arrivals, service) if arrivals > 0 else {"stable": True, "mean_wait_min": 0.0}
            needed = capacity.windows_needed(arrivals, service) if arrivals > 0 else 0
            needed_total += needed
            windows_open = None
            if has_windows:
                per_day: dict[str, set[str]] = defaultdict(set)
                for r in items:
                    if r.get("window") and r.get("call_hour") == hour:
                        per_day[r["day"]].add(r["window"])
                windows_open = sum(len(v) for v in per_day.values()) / day_count
                open_total += windows_open
            hours.append(
                HourStat(
                    hour=hour,
                    arrivals_per_hour=round(arrivals, 1),
                    measured_wait_min=round(sum(waits) / len(waits), 1) if waits else None,
                    measured_wait_p90_min=round(_percentile(waits, 0.9), 1) if waits else None,
                    measured_service_min=round(
                        sum(r["service"] for r in bucket if r.get("service")) / max(1, sum(1 for r in bucket if r.get("service"))), 2
                    )
                    if any(r.get("service") for r in bucket)
                    else None,
                    windows_open=round(windows_open, 1) if windows_open is not None else None,
                    model_wait_min=round(float(model["mean_wait_min"]), 1) if model["stable"] else None,
                    stable=bool(model["stable"]),
                    windows_needed=needed,
                    short=needed > windows,
                )
            )
        all_waits = [r["wait"] for r in items if "wait" in r]
        groups.append(
            GroupHours(
                name=name,
                windows_on_plan=windows,
                service_min=round(service, 2),
                service_source=service_source,
                hours=hours,
                share_waited_over_limit=round(sum(w > MAX_WAIT_MIN for w in all_waits) / len(all_waits), 3)
                if all_waits
                else None,
                staff_hours_needed=needed_total,
                staff_hours_open=round(open_total, 1) if has_windows else None,
            )
        )

    declared = list(plan.flow_hints.peak_hours_local) if plan.flow_hints and plan.flow_hints.peak_hours_local else []
    peaks = _peaks(totals)
    summary: list[str] = []
    for group in groups:
        short = [h for h in group.hours if h.short]
        if short:
            worst = max(short, key=lambda h: h.windows_needed)
            span = ", ".join(f"{h.hour:02d}:00" for h in short)
            when = f"в {span}" if len(short) == 1 else f"в часы {span}"
            summary.append(
                f"{group.name}: {when} нужно {_windows(worst.windows_needed)}, на плане "
                f"{group.windows_on_plan} — без дополнительного окна ожидание в эти часы превышает "
                f"{MAX_WAIT_MIN:g} мин."
            )
        else:
            summary.append(f"{group.name}: {_windows(group.windows_on_plan)} на плане хватает в каждый час данных.")
        if group.share_waited_over_limit == 0:
            summary.append(f"{group.name}: по журналу никто не ждал дольше {MAX_WAIT_MIN:g} мин.")
        elif group.share_waited_over_limit is not None:
            summary.append(
                f"{group.name}: по журналу дольше {MAX_WAIT_MIN:g} мин ждали "
                f"{group.share_waited_over_limit:.0%} посетителей."
            )
        if group.staff_hours_open is not None:
            difference = group.staff_hours_open - group.staff_hours_needed
            if difference > 0.5:
                summary.append(
                    f"{group.name}: по журналу открыто {group.staff_hours_open:g} окно-часов в день, "
                    f"по расчёту достаточно {group.staff_hours_needed} — лишние окна в тихие часы "
                    "можно закрыть, не выходя за норму ожидания."
                )
            elif difference < -0.5:
                summary.append(
                    f"{group.name}: по журналу открыто {group.staff_hours_open:g} окно-часов в день, "
                    f"нужно {group.staff_hours_needed}."
                )
    if declared and peaks and set(peaks) != set(declared):
        summary.append(f"Часы пик по данным — {', '.join(peaks)}; в плане указаны {', '.join(declared)}.")

    return QueueLogReport(
        source=source,
        days=len(days),
        records=len(records),
        skipped_rows=skipped,
        groups=groups,
        peak_hours=peaks,
        declared_peak_hours=declared,
        summary=summary,
        assumptions=list(dict.fromkeys(assumptions)),
    )
