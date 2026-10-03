"""Эксплуатация отделения: обращения о неисправностях, осмотры и сводка «Сегодня».

Обращение создаёт любой сотрудник — с паспорта объекта или отсканировав QR-бирку на нём,
даже без входа. Обращения видны всем, кто открывает план. Брать в работу и закрывать их могут
начальник отделения и инженер: так остаётся след, кто и когда это сделал.

Неисправность с отметкой «не работает» сразу учитывается в расчётах: окно с такой отметкой
считается выключенным в модели очереди и в сводке «Сегодня».
"""

from __future__ import annotations

import os
import uuid
from datetime import date, datetime, timedelta, timezone
from typing import Any, Literal, Protocol

from pydantic import BaseModel, Field, field_validator

from app.accounts import User
from app.assistant import peak
from app.assistant import storage as parcel_storage
from app.models import Plan
from app.rules.engine import Severity, validate

Status = Literal["new", "in_progress", "resolved"]
STATUS_LABELS = {"new": "Новое", "in_progress": "В работе", "resolved": "Решено"}
# Сколько дней вперёд срок осмотра считается близким — так же, как в списке сроков на клиенте.
SOON_DAYS = 30
# Пояс отделения, в котором наступает «сегодня»: сервер в контейнере живёт по UTC, и в первые
# часы московских суток он показывал бы вчерашнюю дату.
DEFAULT_TIMEZONE = "Europe/Moscow"
WEEKDAYS = ("понедельник", "вторник", "среда", "четверг", "пятница", "суббота", "воскресенье")


class Records(Protocol):
    def put_record(self, kind: str, record_id: str, data: dict[str, Any], scope: str = "") -> None: ...
    def get_record(self, kind: str, record_id: str) -> dict[str, Any] | None: ...
    def list_records(self, kind: str, scope: str | None = None) -> list[dict[str, Any]]: ...


class IncidentIn(BaseModel):
    object_id: str = Field(min_length=1, max_length=120)
    text: str = Field(min_length=1, max_length=500, description="Что случилось, своими словами")
    out_of_service: bool = Field(default=False, description="Объект не работает совсем")
    reporter: str | None = Field(default=None, max_length=80, description="Имя, если сотрудник не вошёл")

    @field_validator("text")
    @classmethod
    def _meaningful(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Опишите, что случилось: текст обращения пустой.")
        return value.strip()


class StatusChange(BaseModel):
    status: Status
    note: str | None = Field(default=None, max_length=300)


class HistoryEntry(BaseModel):
    at: str
    status: Status
    by: str
    note: str | None = None


class Incident(BaseModel):
    id: str
    plan_id: str
    object_id: str
    object_label: str
    text: str
    out_of_service: bool
    status: Status
    status_label: str
    created_at: str
    created_by: str
    history: list[HistoryEntry]


def local_today() -> date:
    """Сегодняшняя дата в поясе отделения (OPS_TWIN_TZ, по умолчанию Москва)."""
    name = os.environ.get("OPS_TWIN_TZ", DEFAULT_TIMEZONE)
    try:
        from zoneinfo import ZoneInfo

        zone = ZoneInfo(name)
    except Exception:  # noqa: BLE001 — в образе без базы поясов: Москва без перехода на летнее время
        zone = timezone(timedelta(hours=3))
    return datetime.now(zone).date()


def plural(count: int, one: str, few: str, many: str) -> str:
    """Форма слова для числа: 1 нарушение, 3 нарушения, 21 нарушение, 11 нарушений."""
    tail = abs(count) % 100
    if 11 <= tail <= 14:
        return many
    if tail % 10 == 1:
        return one
    if 2 <= tail % 10 <= 4:
        return few
    return many


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _who(user: User | None, reporter: str | None) -> str:
    if user is not None:
        return f"{user.name}, {user.role_label.lower()}"
    name = (reporter or "").strip()
    return f"{name} (без входа)" if name else "Сотрудник без входа"


def object_label(plan: Plan, object_id: str) -> str | None:
    item = next((e for e in plan.equipment if e.id == object_id), None)
    if item is None:
        return None
    return item.label or item.type


def list_incidents(store: Records, plan_id: str) -> list[Incident]:
    items = [Incident.model_validate(r) for r in store.list_records("incident", plan_id)]
    order = {"new": 0, "in_progress": 1, "resolved": 2}
    return sorted(items, key=lambda i: (order[i.status], i.created_at), reverse=False)


def open_incidents(store: Records, plan_id: str) -> list[Incident]:
    return [i for i in list_incidents(store, plan_id) if i.status != "resolved"]


def report(store: Records, plan_id: str, plan: Plan, payload: IncidentIn, user: User | None) -> Incident:
    label = object_label(plan, payload.object_id)
    if label is None:
        raise KeyError(payload.object_id)
    by = _who(user, payload.reporter)
    at = _now()
    incident = Incident(
        id=uuid.uuid4().hex[:12],
        plan_id=plan_id,
        object_id=payload.object_id,
        object_label=label,
        text=payload.text.strip(),
        out_of_service=payload.out_of_service,
        status="new",
        status_label=STATUS_LABELS["new"],
        created_at=at,
        created_by=by,
        history=[HistoryEntry(at=at, status="new", by=by)],
    )
    store.put_record("incident", incident.id, incident.model_dump(), plan_id)
    return incident


def change_status(store: Records, plan_id: str, incident_id: str, change: StatusChange, user: User) -> Incident:
    record = store.get_record("incident", incident_id)
    if record is None or record.get("plan_id") != plan_id:
        raise KeyError(incident_id)
    incident = Incident.model_validate(record)
    note = (change.note or "").strip() or None
    # Тот же статус без комментария — повторное нажатие, а не новое событие: в журнал не пишется.
    if change.status == incident.status and note is None:
        return incident
    incident.status = change.status
    incident.status_label = STATUS_LABELS[change.status]
    incident.history.append(
        HistoryEntry(at=_now(), status=change.status, by=_who(user, None), note=note)
    )
    store.put_record("incident", incident.id, incident.model_dump(), plan_id)
    return incident


class InspectionDone(BaseModel):
    object_id: str
    next_inspection: date | None = Field(
        default=None, description="Срок следующего осмотра; без него берётся прежний интервал"
    )
    done_on: date | None = Field(
        default=None, description="Когда выполнен осмотр — день по часам сотрудника; без него — сегодня в поясе отделения"
    )


def mark_inspected(document: dict[str, Any], payload: InspectionDone, today: date | None = None) -> tuple[dict[str, Any], str]:
    """Отмечает осмотр в паспорте объекта. Возвращает новый документ и срок следующего осмотра.

    ValueError — следующий срок не задан и прежнего интервала нет: выдумывать его сервис не будет.
    """
    today = today or payload.done_on or local_today()
    item = next((e for e in document.get("equipment", []) if e.get("id") == payload.object_id), None)
    if item is None:
        raise KeyError(payload.object_id)
    inventory = dict(item.get("inventory") or {})
    following = payload.next_inspection
    if following is None:
        try:
            last = date.fromisoformat(str(inventory.get("last_inspection")))
            due = date.fromisoformat(str(inventory.get("next_inspection")))
        except ValueError:
            raise ValueError("Укажите срок следующего осмотра: прежнего интервала в паспорте нет.") from None
        interval = (due - last).days
        if interval <= 0:
            raise ValueError("Укажите срок следующего осмотра: прежний интервал в паспорте некорректен.")
        following = today + timedelta(days=interval)
    if following <= today:
        raise ValueError("Следующий осмотр должен быть позже сегодняшнего дня.")
    inventory["last_inspection"] = today.isoformat()
    inventory["next_inspection"] = following.isoformat()
    item["inventory"] = inventory
    return document, following.isoformat()


class InspectionDue(BaseModel):
    object_id: str
    label: str
    due: str
    days: int = Field(description="Дней до срока; меньше нуля — просрочено")


class WindowsToday(BaseModel):
    name: str
    total: int
    working: int
    needed: int
    mean_wait_min: float | None
    meets_target: bool


class Action(BaseModel):
    text: str
    kind: Literal["incident", "inspection", "windows", "check", "storage"]
    object_id: str | None = None


class Today(BaseModel):
    date: str
    weekday: str
    plan_title: str
    user: User | None = None
    incidents: list[Incident]
    out_of_service: list[str]
    inspections: list[InspectionDue]
    windows: list[WindowsToday]
    windows_note: str | None = None
    target_wait_min: float | None = None
    errors: int
    warnings: int
    storage_short: int | None = None
    actions: list[Action]


def inspections_due(plan: Plan, today: date) -> list[InspectionDue]:
    found = []
    for item in plan.equipment:
        raw = item.inventory.next_inspection if item.inventory else None
        if not raw:
            continue
        try:
            due = date.fromisoformat(str(raw))
        except ValueError:
            continue
        days = (due - today).days
        if days <= SOON_DAYS:
            found.append(InspectionDue(object_id=item.id, label=item.label or item.type, due=due.isoformat(), days=days))
    return sorted(found, key=lambda i: i.days)


def _days(n: int) -> str:
    n = abs(n)
    word = "день" if n % 10 == 1 and n % 100 != 11 else "дня" if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14 else "дней"
    return f"{n} {word}"


def today_summary(plan: Plan, incidents: list[Incident], user: User | None, today: date | None = None) -> Today:
    today = today or local_today()
    broken_ids = sorted({i.object_id for i in incidents if i.out_of_service and i.status != "resolved"})
    labels = {e.id: e.label or e.type for e in plan.equipment}
    actions: list[Action] = []

    for incident in incidents:
        if incident.status == "new":
            actions.append(
                Action(
                    text=f"Разобрать обращение: «{incident.object_label}» — {incident.text}",
                    kind="incident",
                    object_id=incident.object_id,
                )
            )

    inspections = inspections_due(plan, today)
    for due in inspections:
        when = f"просрочен на {_days(due.days)}" if due.days < 0 else "сегодня" if due.days == 0 else f"через {_days(due.days)}"
        actions.append(Action(text=f"Осмотр «{due.label}» — {when}", kind="inspection", object_id=due.object_id))

    windows: list[WindowsToday] = []
    note = None
    target = None
    peak_report = peak.analyze(plan, peak.PeakScenario(out_of_service=[i for i in broken_ids if i in labels]))
    if peak_report.available:
        target = peak_report.target_wait_min
        for group in peak_report.groups:
            windows.append(
                WindowsToday(
                    name=group.name,
                    total=len(group.windows),
                    working=len(group.working),
                    needed=group.windows_needed,
                    mean_wait_min=group.mean_wait_min,
                    meets_target=group.meets_target,
                )
            )
            if len(group.working) < group.windows_needed:
                lack = group.windows_needed - len(group.working)
                spare = len(group.windows) - len(group.working)
                if spare:
                    actions.append(
                        Action(
                            text=f"{group.name}: в пик нужно {group.windows_needed}, работает {len(group.working)} — "
                            f"откройте или почините ещё {min(lack, spare)}",
                            kind="windows",
                        )
                    )
                else:
                    actions.append(
                        Action(
                            text=f"{group.name}: в пик нужно {group.windows_needed}, а окон всего {len(group.windows)} — "
                            f"очередь будет дольше {target:g} мин",
                            kind="windows",
                        )
                    )
    else:
        note = peak_report.reason

    findings = validate(plan).findings
    errors = sum(1 for f in findings if f.severity is Severity.ERROR)
    warnings = sum(1 for f in findings if f.severity is Severity.WARNING)
    if errors:
        # Те же слова и тот же экран, что в «Обзоре»: там проблемы плана собраны с исправлением.
        word = plural(errors, "проблема", "проблемы", "проблем")
        actions.append(Action(text=f"На плане {errors} {word} — откройте «Обзор»", kind="check"))

    storage_report = parcel_storage.analyze(plan)
    storage_short = None
    if storage_report.available:
        storage_short = storage_report.deficit
        if storage_report.deficit:
            actions.append(
                Action(text=f"Посылкам не хватает {storage_report.deficit} мест по норме ВНТП", kind="storage")
            )

    return Today(
        date=today.isoformat(),
        weekday=WEEKDAYS[today.weekday()],
        plan_title=plan.ops.name or plan.ops.id,
        user=user,
        incidents=[i for i in incidents if i.status != "resolved"],
        out_of_service=[labels.get(i, i) for i in broken_ids],
        inspections=inspections,
        windows=windows,
        windows_note=note,
        target_wait_min=target,
        errors=errors,
        warnings=warnings,
        storage_short=storage_short,
        actions=actions,
    )
