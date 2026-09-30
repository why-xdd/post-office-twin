"""Ассистент планировки: рекомендации с объяснением и эффектом.

Сценарии из ТЗ:
- поиск места для дополнительного рабочего места, окна обслуживания или стеллажа;
- выявление отсутствующего или лишнего оборудования;
- оценка влияния изменений на свободную площадь и инвентаризацию.

Каждая рекомендация — это набор изменений, которые можно применить в редакторе, причины со
ссылками на правило и пункт документа, явные допущения и сравнение показателей до и после.
Все числа вычисляются; текст объяснения собирается из вычисленных фактов.
"""

from __future__ import annotations

import os
import time
from typing import Any

from pydantic import BaseModel, Field
from shapely.geometry import LineString
from shapely.geometry import Point as ShapelyPoint
from shapely.ops import unary_union

from app.assistant import capacity as capacity_model
from app.assistant import language
from app.assistant import llm
from app.assistant import objective
from app.assistant import completeness as completeness_model
from app.assistant import search
from app.dictionaries import equipment_types
from app.geometry import core, passability
from app.models import Equipment, Plan
from app.rules.engine import SERVICE_WINDOW_TYPES, VISITOR_TARGET_TYPES, Severity, validate

# Объекты, которые разумно двигать первыми: их перенос дешевле перепланировки окон.
# Окно кассы встроено в перегородку между залом и служебной зоной: к нему подведены кабели,
# стоит кассовое оборудование и охрана. Движок его не переставляет и не поворачивает — двигает
# остальное оборудование, а если без окна нарушение не снять, прямо говорит об этом.
BUILT_IN_TYPES = frozenset({"cashier_window"})
BUILT_IN_ASSUMPTION = (
    "Окна кассы встроены в перегородку между залом и служебной зоной и не переставляются: "
    "двигается остальное оборудование."
)
BUILT_IN_LEFT = (
    "Часть нарушений связана с окнами кассы. Окна встроены в перегородку, поэтому автоматически "
    "не переставляются: такие нарушения снимает перепланировка, решение за проектировщиком."
)
BUILT_IN_REFUSAL = (
    "Окна кассы встроены в перегородку между залом и служебной зоной: ассистент их не переставляет "
    "и не поворачивает. Если перепланировка это допускает, передвиньте окно на плане вручную."
)

MOVE_PRIORITY = {
    "waiting_seat": 0,
    "info_stand": 0,
    "potted_plant": 0,
    "trash_bin": 0,
    "goods_rack": 1,
    "atm": 1,
    "self_service_kiosk": 1,
    "package_drop_box": 1,
    "weighing_station": 1,
    "storage_rack": 2,
    "safe": 2,
    "back_office_workplace": 3,
    "pvz_counter": 4,
    "cashier_window": 4,
}
FIX_TOP_K = 10
FIX_WIDE_TOP_K = 40
MAX_FIX_OBJECTS = 6
LOW_RESERVE_PERCENT = 25.0


class Reason(BaseModel):
    text: str
    source: str | None = None
    clause: str | None = None


class Effect(BaseModel):
    key: str
    label: str
    before: float | int | None
    after: float | int | None
    unit: str = ""
    improved: bool | None = None


class Change(BaseModel):
    op: str = Field(description="add, move или remove")
    object_id: str
    equipment_type: str
    label: str | None = None
    position: tuple[float, float] | None = None
    rotation_deg: float | None = None
    size_m: tuple[float, float] | None = None
    zone: str | None = None
    operator_position: tuple[float, float] | None = None
    previous_position: tuple[float, float] | None = None
    previous_rotation_deg: float | None = None


class Recommendation(BaseModel):
    id: str
    scenario: str = Field(description="fix, missing, excess, capacity, placement")
    priority: int = Field(description="0 — самое важное")
    title: str
    summary: str
    changes: list[Change]
    reasons: list[Reason]
    assumptions: list[str] = Field(default_factory=list)
    effects: list[Effect]
    objects: list[str] = Field(default_factory=list)
    zones: list[str] = Field(default_factory=list)
    alternatives: list[Change] = Field(default_factory=list)
    search: dict[str, float | int] = Field(default_factory=dict)


class AssistantReport(BaseModel):
    recommendations: list[Recommendation]
    capacity: capacity_model.CapacityReport
    completeness: list[completeness_model.CompletenessIssue]
    summary: dict[str, Any]
    elapsed_ms: float
    partial: bool = Field(
        default=False, description="Отведённое время кончилось: показано то, что успели подобрать"
    )


def _budget() -> float:
    """Сколько секунд ассистент тратит на один запрос."""
    try:
        return max(1.0, float(os.environ.get("OPS_TWIN_ASSISTANT_BUDGET", DEFAULT_BUDGET_S)))
    except ValueError:
        return DEFAULT_BUDGET_S


# Зал в 400 м² с шестью десятками объектов даёт сотни вариантов расстановки на каждое нарушение,
# и полный перебор рекомендаций там занимает больше минуты. Честный неполный ответ за разумное
# время полезнее: человек видит найденное и знает, что подбор остановлен по времени.
DEFAULT_BUDGET_S = 25.0


class Deadline:
    """Отсечка по времени для подбора рекомендаций."""

    def __init__(self, seconds: float | None = None) -> None:
        self.until = time.perf_counter() + (seconds if seconds is not None else _budget())
        self.reached = False

    def spent(self) -> bool:
        if not self.reached and time.perf_counter() >= self.until:
            self.reached = True
        return self.reached


def _narrowest_targets(routes: list[passability.RouteWidth]) -> list[str]:
    reachable = [r for r in routes if r.reachable]
    if not reachable:
        return []
    narrowest = min(r.width_m for r in reachable)
    return [r.target_id for r in reachable if r.width_m == narrowest]


def summarize(plan: Plan) -> dict[str, Any]:
    report = validate(plan, explain=False)
    routes = passability.client_route_widths(plan, VISITOR_TARGET_TYPES)
    capacity = capacity_model.analyze(plan)
    counts: dict[str, int] = {}
    for item in plan.equipment:
        counts[item.type] = counts.get(item.type, 0) + 1
    return {
        "errors": report.errors,
        "warnings": report.warnings,
        "total_area": core.total_area(plan),
        "occupied_area": core.occupied_area(plan),
        "free_area": core.free_area(plan),
        "equipment_total": len(plan.equipment),
        "equipment_by_type": counts,
        "min_route_width": min((r.width_m for r in routes), default=None),
        "walk_m": objective.walk_distance(plan),
        "entrance_route_width": max((r.width_m for r in routes if r.reachable), default=None),
        "narrowest_targets": _narrowest_targets(routes),
        "waiting_capacity": capacity.waiting_capacity_persons,
        "peak_queue": capacity.peak_queue_persons,
        "waits": {g.name: g.mean_wait_min for g in capacity.groups},
        "reserve": {g.name: g.reserve_percent for g in capacity.groups},
    }


def _effects(before: dict[str, Any], after: dict[str, Any], type_code: str | None = None) -> list[Effect]:
    def eff(key: str, label: str, unit: str, lower_is_better: bool) -> Effect:
        b, a = before.get(key), after.get(key)
        improved = None
        if isinstance(b, (int, float)) and isinstance(a, (int, float)) and a != b:
            improved = (a < b) if lower_is_better else (a > b)
        return Effect(key=key, label=label, before=b, after=a, unit=unit, improved=improved)

    effects = [
        eff("errors", "Нарушений", "", True),
        eff("warnings", "Замечаний", "", True),
        eff("free_area", "Свободная площадь", "м²", False),
        eff("occupied_area", "Занято оборудованием", "м²", True),
        eff("entrance_route_width", "Ширина прохода от входа", "м", False),
        eff("min_route_width", "Самое узкое место пути посетителя", "м", False),
        eff("waiting_capacity", "Вместимость зоны ожидания", "чел.", False),
    ]
    # Площади сами по себе не хороши и не плохи: новый предмет всегда занимает место, и
    # помечать это «хуже» — значит ругать любую покупку. Качество видно по нарушениям и проходам.
    effects[2].improved = None
    effects[3].improved = None
    if type_code is not None:
        effects.append(
            Effect(
                key=f"count:{type_code}",
                label="Единиц этого типа в инвентаризации",
                before=before["equipment_by_type"].get(type_code, 0),
                after=after["equipment_by_type"].get(type_code, 0),
            )
        )
    for name, wait in after.get("waits", {}).items():
        prev = before.get("waits", {}).get(name)
        if prev != wait:
            effects.append(
                Effect(
                    key=f"wait:{name}",
                    label=f"Ожидание в час пик — {name.lower()}",
                    before=prev,
                    after=wait,
                    unit="мин",
                    improved=None if prev is None or wait is None else wait < prev,
                )
            )
    return [e for e in effects if not (e.before is None and e.after is None)]


def _label(plan: Plan, type_code: str) -> str:
    known = equipment_types(plan).get(type_code)
    return known.label if known else type_code


def _object_name(plan: Plan, object_id: str) -> str:
    item = next((e for e in plan.equipment if e.id == object_id), None)
    if item is None:
        return object_id
    name = item.label or _label(plan, item.type)
    # Внутри фразы название пишется со строчной: «переставить окно кассы №1», но «АТМ» и
    # другие аббревиатуры остаются как есть.
    if len(name) > 1 and name[0].isupper() and name[1].islower():
        name = name[0].lower() + name[1:]
    return f"{name} ({item.id})"


def _change_from(plan: Plan, evaluated: search.Evaluated, op: str, object_id: str, type_code: str, template: Equipment | None = None) -> Change:
    item = next(e for e in evaluated.plan.equipment if e.id == object_id)
    return Change(
        op=op,
        object_id=object_id,
        equipment_type=type_code,
        label=item.label,
        position=item.position,
        rotation_deg=item.rotation_deg,
        size_m=item.size_m,
        zone=item.zone,
        operator_position=item.operator_position,
        previous_position=template.position if template else None,
        previous_rotation_deg=template.rotation_deg if template else None,
    )


def _zone_label(plan: Plan, zone_id: str | None) -> str:
    zone = next((z for z in plan.zones if z.id == zone_id), None)
    return zone.label or zone.id if zone else "вне зон"


def _unique_id(plan: Plan, base: str) -> str:
    existing = {e.id for e in plan.equipment}
    index = 1
    while f"{base}_{index}" in existing:
        index += 1
    return f"{base}_{index}"


PATH_GAIN_M = 0.05
PATH_FIX_ROUNDS = 3
PATH_FIX_TOP_K = 24


def recommend_path_fix(plan: Plan, before: dict[str, Any]) -> Recommendation | None:
    """Расширение прохода у входа, зажатого несколькими объектами сразу.

    Перенос одного виновника может расширить путь, но не снять ошибку, пока путь уже нормы.
    Поэтому виновники переставляются последовательно, и рекомендация принимается по росту
    ширины общего участка пути у входа, а не только по числу ошибок. Ширина меряется по самому
    свободному маршруту: местные сужения у отдельных окон — предмет других рекомендаций.
    """
    from app.rules.catalog import MIN_PATH_WITH_POCKETS_M

    narrowest = before.get("min_route_width")
    if narrowest is None or narrowest >= MIN_PATH_WITH_POCKETS_M:
        return None
    width_before = passability.widest_route_width(plan, VISITOR_TARGET_TYPES)
    if width_before is None or width_before >= MIN_PATH_WITH_POCKETS_M:
        return None
    current = plan
    current_width = width_before
    changes: list[Change] = []
    moved: set[str] = set()
    stats: dict[str, float | int] = {}
    frozen = False
    # После перестановки виновников сужение может сместиться к другому объекту, поэтому
    # анализ повторяется на обновлённом плане, пока проход не достигнет нормы.
    for _round in range(PATH_FIX_ROUNDS):
        if current_width >= MIN_PATH_WITH_POCKETS_M:
            break
        blockers = passability.bottleneck_blockers(current, VISITOR_TARGET_TYPES, MIN_PATH_WITH_POCKETS_M)
        # Уже переставленный объект может остаться виновником, если первое место было лишь
        # локально лучшим; повторный перенос допускается, пока ширина прохода растёт.
        culprits = blockers.get(current_width, [])
        if not culprits:
            break
        progressed = False
        for object_id in culprits:
            item = next((e for e in current.equipment if e.id == object_id), None)
            if item is not None and item.type in BUILT_IN_TYPES:
                frozen = True
                continue
            if item is None:
                continue
            found = search.place(
                current, item.type, new_id=item.id, template=item, top_k=PATH_FIX_TOP_K, keep=8
            )
            stats = _stats(found)
            options = [
                (passability.widest_route_width(e.plan, VISITOR_TARGET_TYPES) or 0.0, e) for e in found.best
            ]
            if not options:
                continue
            # При равной ширине прохода выбирается вариант, который лучше для плана в целом:
            # места ожидания, путь посетителя и величина переноса.
            best_width, best = max(
                options,
                key=lambda o: (round(o[0], 2), -o[1].errors, -(o[1].score.total if o[1].score else 0.0)),
            )
            if best_width < current_width + 0.01:
                continue
            original = next(e for e in plan.equipment if e.id == item.id)
            changes = [c for c in changes if c.object_id != item.id]
            changes.append(_change_from(current, best, "move", item.id, item.type, template=original))
            moved.add(item.id)
            current = best.plan
            current_width = best_width
            progressed = True
        if not progressed:
            break

    if not changes:
        return None
    after = summarize(current)
    if current_width < width_before + PATH_GAIN_M and after["errors"] >= before["errors"]:
        return None

    names = ", ".join(_object_name(plan, c.object_id) for c in changes)
    reaches_norm = current_width >= MIN_PATH_WITH_POCKETS_M
    remaining = ""
    narrowest_after = after.get("min_route_width")
    if narrowest_after is not None and narrowest_after < MIN_PATH_WITH_POCKETS_M:
        targets = ", ".join(_object_name(current, t) for t in after.get("narrowest_targets", []))
        remaining = (
            f" Местное сужение до {narrowest_after} м остаётся на подходе: {targets} — "
            "его снимают отдельные рекомендации."
        )
    trade_off = ""
    if (
        isinstance(before.get("waiting_capacity"), (int, float))
        and isinstance(after.get("waiting_capacity"), (int, float))
        and after["waiting_capacity"] < before["waiting_capacity"]
    ):
        trade_off = (
            f" Цена решения: вместимость зоны ожидания {before['waiting_capacity']} → "
            f"{after['waiting_capacity']} чел."
        )
    return Recommendation(
        id="fix:path:" + "+".join(c.object_id for c in changes),
        scenario="fix",
        priority=0,
        title="Освободить проход от входа",
        summary=(
            f"Переставить: {names}. Ширина прохода от входа {width_before} → {current_width} м"
            + ("" if reaches_norm else f" (норма не менее {MIN_PATH_WITH_POCKETS_M} м пока не достигнута)")
            + f". Ошибок станет {after['errors']} вместо {before['errors']}."
            + remaining
            + trade_off
        ),
        changes=changes,
        reasons=[
            Reason(
                text=(
                    f"Проход от входа зажат объектами: {names} — ширина {width_before} м, посетитель на "
                    f"кресле-коляске не проходит."
                ),
                source="СП 59.13330.2020 «Доступность зданий и сооружений для МГН»",
                clause="п. 6.2.1",
            )
        ],
        assumptions=[
            "Виновники сужения определены расчётом: без них проход от входа становится шире. "
            "Каждый переставлен на место с наибольшей шириной прохода, не добавляющее ошибок.",
            *([BUILT_IN_ASSUMPTION] if frozen else []),
        ],
        effects=_effects(before, after),
        objects=[c.object_id for c in changes],
        zones=[c.zone for c in changes if c.zone],
        search=stats,
    )


def recommend_fixes(
    plan: Plan,
    before: dict[str, Any],
    limit: int | None = None,
    deadline: "Deadline | None" = None,
) -> list[Recommendation]:
    """Для объектов, участвующих в ошибках, ищется перенос, который уменьшает число ошибок.

    limit останавливает поиск после стольких найденных рекомендаций: при пошаговом улучшении
    применяется одна, и перебирать остальные незачем. deadline прекращает перебор объектов,
    когда отведённое на подбор время кончилось.
    """
    path_fix = recommend_path_fix(plan, before)
    if path_fix is not None and limit == 1:
        return [path_fix]
    report = validate(plan)
    errors = [f for f in report.findings if f.severity is Severity.ERROR]
    involved: dict[str, list[Any]] = {}
    for finding in errors:
        for object_id in finding.objects:
            involved.setdefault(object_id, []).append(finding)

    involved_items = [item for item in core.floor_equipment(plan) if item.id in involved]
    movable = [item for item in involved_items if item.type not in BUILT_IN_TYPES]
    frozen = len(movable) < len(involved_items)
    movable.sort(key=lambda i: (MOVE_PRIORITY.get(i.type, 2), -len(involved[i.id])))
    movable = movable[:MAX_FIX_OBJECTS]

    results: list[Recommendation] = [path_fix] if path_fix is not None else []
    for item in movable:
        if limit is not None and len(results) >= limit:
            break
        if results and deadline is not None and deadline.spent():
            break
        found = search.place(
            plan,
            item.type,
            new_id=item.id,
            template=item,
            top_k=FIX_TOP_K,
            keep=2,
            prefer_near=item.position,
        )
        # Вариант принимается, если план в целом становится лучше и ошибок не прибавилось:
        # расширение прохода с 0,64 до 0,9 м ошибку пока не снимает, но уже полезно.
        here = objective.score(
            plan,
            errors=before["errors"],
            warnings=before["warnings"],
            entrance_width=before.get("entrance_route_width"),
            narrowest_route=before.get("min_route_width"),
            with_queue=False,
        )

        def better(options: list[search.Evaluated]) -> list[search.Evaluated]:
            return [
                e
                for e in options
                if e.errors <= before["errors"] and e.score is not None and e.score.better_than(here, margin=0.5)
            ]

        improving = better(found.best)
        if not improving:
            # Рядом с текущим местом подходящего нет — проверяется больше мест по всей зоне.
            found = search.place(plan, item.type, new_id=item.id, template=item, top_k=FIX_WIDE_TOP_K, keep=2)
            improving = better(found.best)
        if not improving:
            continue
        best = improving[0]
        after = summarize(best.plan)
        fixed = before["errors"] - after["errors"]
        problems, remaining = _resolved(plan, best.plan, item.id)
        if not problems:
            continue
        moved = ((best.candidate.position[0] - item.position[0]) ** 2 + (best.candidate.position[1] - item.position[1]) ** 2) ** 0.5
        results.append(
            Recommendation(
                id=f"fix:{item.id}",
                scenario="fix",
                priority=0,
                title=f"Переставить: {_object_name(plan, item.id)}",
                summary=(
                    f"Перенос на {moved:.2f} м в зону «{_zone_label(best.plan, best.candidate and next(e for e in best.plan.equipment if e.id == item.id).zone)}» "
                    f"убирает ошибок: {fixed}. Ошибок станет {after['errors']} вместо {before['errors']}."
                ),
                changes=[_change_from(plan, best, "move", item.id, item.type, template=item)],
                reasons=[
                    Reason(text=f.message, source=f.source, clause=f.clause) for f in problems
                ],
                assumptions=[
                    "Новое место выбрано так, чтобы не добавить ни одной ошибки проверки и не сузить "
                    "путь посетителя; из допустимых мест предпочтено ближайшее к текущему.",
                    *([BUILT_IN_ASSUMPTION] if frozen else []),
                    *(
                        [
                            "Перенос не устраняет: "
                            + "; ".join(f.message for f in remaining)
                        ]
                        if remaining
                        else []
                    ),
                ],
                effects=_effects(before, after, item.type),
                objects=[item.id, *{o for f in problems for o in f.objects if o != item.id}],
                zones=[z for z in {item.zone, next(e for e in best.plan.equipment if e.id == item.id).zone} if z],
                alternatives=[
                    _change_from(plan, alt, "move", item.id, item.type, template=item)
                    for alt in improving[1:]
                ],
                search=_stats(found),
            )
        )
    # Освобождение прохода от входа идёт первым: пока посетитель на кресле-коляске не может
    # войти, остальные исправления планировки для него не имеют значения.
    results.sort(
        key=lambda r: (
            0 if r.id.startswith("fix:path:") else 1,
            min((e.after for e in r.effects if e.key == "errors"), default=0),
        )
    )
    return results


def _stats(found: search.SearchResult) -> dict[str, float | int]:
    return {
        "generated": found.generated,
        "passed_fast_filter": found.passed_fast_filter,
        "fully_checked": found.fully_checked,
        "elapsed_ms": found.elapsed_ms,
    }


PUBLIC_ZONE_TYPES = {"client_area", "waiting", "queue_accumulator", "vestibule"}


def _preferred_zone(plan: Plan, type_code: str) -> str | None:
    """Камера и табло нужны в клиентской части, пожарный извещатель — в части без извещателя."""
    if type_code in ("camera", "info_display", "media_panel"):
        zone = next((z for z in plan.zones if z.type == "client_area"), None)
        return zone.id if zone else None
    if type_code == "fire_alarm":
        zone_types_by_id = {z.id: z.type for z in plan.zones}
        covered_public = any(
            zone_types_by_id.get(e.zone or "") in PUBLIC_ZONE_TYPES
            for e in core.equipment_by_type(plan, "fire_alarm")
        )
        wanted_public = not covered_public
        candidates = [z for z in plan.zones if (z.type in PUBLIC_ZONE_TYPES) == wanted_public]
        zone = max(candidates, key=lambda z: core.zone_polygon(z).area, default=None)
        return zone.id if zone else None
    return None


def _finding_key(finding: Any) -> tuple[str, frozenset[str]]:
    return finding.rule_id, frozenset(finding.objects)


def _resolved(before_plan: Plan, after_plan: Plan, object_id: str) -> tuple[list[Any], list[Any]]:
    """Ошибки с участием объекта, которые изменение устранило, и которые остались."""
    before = [f for f in validate(before_plan).findings if f.severity is Severity.ERROR and object_id in f.objects]
    after_keys = {_finding_key(f) for f in validate(after_plan).findings if f.severity is Severity.ERROR}
    resolved = [f for f in before if _finding_key(f) not in after_keys]
    remaining = [f for f in before if _finding_key(f) in after_keys]
    return resolved, remaining


def recommend_completeness(
    plan: Plan,
    before: dict[str, Any],
    capacity: capacity_model.CapacityReport,
    limit: int | None = None,
    deadline: "Deadline | None" = None,
) -> list[Recommendation]:
    results: list[Recommendation] = []
    working = plan
    for issue in completeness_model.analyze(plan, capacity):
        if limit is not None and sum(1 for r in results if r.changes) >= limit:
            break
        if results and deadline is not None and deadline.spent():
            break
        label = _label(plan, issue.equipment_type)
        reason = Reason(text=issue.message.replace(f"«{issue.equipment_type}»", f"«{label}»"), source=issue.source, clause=issue.clause)
        assumptions = [issue.assumption] if issue.assumption else []

        if issue.kind == "excess":
            document = working.model_dump(by_alias=True, exclude_none=True)
            document["equipment"] = [e for e in document["equipment"] if e["id"] not in issue.objects]
            after_plan = Plan.model_validate(document)
            after = summarize(after_plan)
            results.append(
                Recommendation(
                    id=f"excess:{issue.rule_id}",
                    scenario="excess",
                    priority=2,
                    title=f"Убрать лишнее: {label}",
                    summary=f"Сверх нормы комплектования: {', '.join(issue.objects)}. "
                    f"Освобождается {_metres((after['free_area'] or 0) - (before['free_area'] or 0))} м².",
                    changes=[
                        Change(op="remove", object_id=oid, equipment_type=issue.equipment_type, label=label)
                        for oid in issue.objects
                    ],
                    reasons=[reason],
                    assumptions=assumptions,
                    effects=_effects(before, after, issue.equipment_type),
                    objects=issue.objects,
                )
            )
            continue

        missing = issue.required - issue.present
        added_changes: list[Change] = []
        alternatives: list[Change] = []
        stats: dict[str, float | int] = {}
        candidate_plan = working
        for _ in range(missing):
            new_id = _unique_id(candidate_plan, issue.equipment_type)
            found = search.place(
                candidate_plan,
                issue.equipment_type,
                new_id=new_id,
                zone_id=_preferred_zone(candidate_plan, issue.equipment_type),
                top_k=12,
                keep=3,
            )
            stats = _stats(found)
            if not found.best:
                break
            best = found.best[0]
            added_changes.append(_change_from(candidate_plan, best, "add", new_id, issue.equipment_type))
            if not alternatives:
                alternatives = [_change_from(candidate_plan, alt, "add", new_id, issue.equipment_type) for alt in found.best[1:]]
            candidate_plan = best.plan

        if not added_changes:
            results.append(
                Recommendation(
                    id=f"missing:{issue.rule_id}",
                    scenario="missing",
                    priority=1,
                    title=f"Не хватает: {label}",
                    summary="Свободного места, где объект не нарушит ни одного правила, на плане нет. "
                    "Нужна перестановка — сначала выполните рекомендации по исправлению ошибок.",
                    changes=[],
                    reasons=[reason],
                    assumptions=assumptions,
                    effects=[],
                    search=stats,
                )
            )
            continue

        after = summarize(candidate_plan)
        places = "; ".join(
            f"{_place_words(c.position)} в зоне «{_zone_label(candidate_plan, c.zone)}»"
            for c in added_changes
            if c.position
        )
        results.append(
            Recommendation(
                id=f"missing:{issue.rule_id}",
                scenario="missing",
                priority=1,
                title=f"Добавить: {label}" + (f" × {len(added_changes)}" if len(added_changes) > 1 else ""),
                summary=f"Предлагаемое место: {places}. Ошибок проверки не добавляется, "
                f"свободная площадь уменьшится на "
                f"{_metres((before['free_area'] or 0) - (after['free_area'] or 0))} м².",
                changes=added_changes,
                reasons=[reason],
                assumptions=assumptions,
                effects=_effects(before, after, issue.equipment_type),
                objects=[c.object_id for c in added_changes],
                zones=[c.zone for c in added_changes if c.zone],
                alternatives=alternatives,
                search=stats,
            )
        )
    return results


def recommend_capacity(
    plan: Plan,
    before: dict[str, Any],
    capacity: capacity_model.CapacityReport,
    limit: int | None = None,
    deadline: "Deadline | None" = None,
) -> list[Recommendation]:
    results: list[Recommendation] = []
    if not capacity.available:
        return results
    for group in capacity.groups:
        if limit is not None and sum(1 for r in results if r.changes) >= limit:
            break
        if results and deadline is not None and deadline.spent():
            break
        short = len(group.windows) < group.windows_needed
        low_reserve = group.reserve_percent is not None and group.reserve_percent < LOW_RESERVE_PERCENT
        if not (short or low_reserve):
            continue
        type_code = "pvz_counter" if group.name == "Выдача посылок" else "cashier_window"
        new_id = _unique_id(plan, type_code)
        found = search.place(plan, type_code, new_id=new_id, top_k=12, keep=3)
        reasons = [
            Reason(
                text=(
                    f"{group.name}: в час пик {group.arrivals_per_hour} посетителей в час на "
                    f"{len(group.windows)} окн., загрузка {round(group.utilization * 100)}%, "
                    f"среднее ожидание {group.mean_wait_min if group.stable else 'растёт без предела'} мин."
                ),
                source="Расчёт пропускной способности (модель M/M/c)",
            )
        ]
        if group.service_threshold_min is not None:
            reasons.append(
                Reason(
                    text=f"Текущие окна справляются, пока среднее обслуживание не дольше "
                    f"{group.service_threshold_min} мин; сейчас {group.service_min} мин, "
                    f"запас {group.reserve_percent}%."
                )
            )
        if not found.best:
            results.append(
                Recommendation(
                    id=f"capacity:{type_code}:{new_id}",
                    scenario="capacity",
                    priority=1,
                    title=f"Не хватает пропускной способности: {group.name.lower()}",
                    summary="Места для дополнительного окна без нарушения правил на плане нет.",
                    changes=[],
                    reasons=reasons,
                    assumptions=capacity.assumptions,
                    effects=[],
                    objects=group.windows,
                    search=_stats(found),
                )
            )
            continue
        best = found.best[0]
        after = summarize(best.plan)
        results.append(
            Recommendation(
                id=f"capacity:{type_code}:{new_id}",
                scenario="capacity",
                priority=1 if short else 3,
                title=f"Добавить окно: {group.name.lower()}",
                summary=(
                    f"С дополнительным окном ожидание в час пик "
                    f"{before['waits'].get(group.name)} → {after['waits'].get(group.name)} мин, "
                    f"запас по времени обслуживания {before['reserve'].get(group.name)}% → "
                    f"{after['reserve'].get(group.name)}%."
                ),
                changes=[_change_from(plan, best, "add", new_id, type_code)],
                reasons=reasons,
                assumptions=capacity.assumptions,
                effects=_effects(before, after, type_code),
                objects=group.windows,
                zones=[z for z in [next(e for e in best.plan.equipment if e.id == new_id).zone] if z],
                alternatives=[_change_from(plan, alt, "add", new_id, type_code) for alt in found.best[1:]],
                search=_stats(found),
            )
        )
    return results


def _metres(value: float | None) -> str:
    """Число для текста: два знака и запятая, как в таблицах интерфейса."""
    if value is None:
        return "—"
    return f"{value:.2f}".replace(".", ",")


def _place_words(position: tuple[float, float]) -> str:
    """Место объекта словами: оси подписаны, дробная часть через запятую."""
    return f"X {_metres(position[0])} · Y {_metres(position[1])} м"


def _near(plan: Plan, action: "language.Action | None") -> "search.Near | None":
    """Условие места из фразы: у перегородки, у входа, у стены. Без условия — None."""
    if action is None:
        return None
    if action.near_partition:
        lines = [LineString([w.from_, w.to]) for w in plan.walls if w.type != "outer"]
        if lines:
            # Окно у перегородки: между ним и стеной помещается рабочее место оператора.
            return search.Near(unary_union(lines), 1.6, "у перегородки")
    if action.near_entrance:
        entrance = passability.entrance_point(plan)
        if entrance is not None:
            return search.Near(ShapelyPoint(entrance), 3.0, "у входа")
    if action.near_wall:
        outline = core.outline(plan)
        if outline is not None:
            return search.Near(outline.exterior, 0.6, "у стены")
    return None


def place_equipment(
    plan: Plan, type_code: str, zone_id: str | None = None, action: "language.Action | None" = None
) -> Recommendation:
    """Сценарий «найти место для дополнительного рабочего места, окна или стеллажа»."""
    before = summarize(plan)
    new_id = _unique_id(plan, type_code)
    label = _label(plan, type_code)
    near = _near(plan, action)
    found = search.place(plan, type_code, new_id=new_id, zone_id=zone_id, keep=3, near=near)
    missed = None
    if near is not None and not found.best:
        # Условие не выполнить: сказать об этом прямо и предложить лучшее место без него.
        missed = near.words
        found = search.place(plan, type_code, new_id=new_id, zone_id=zone_id, keep=3)
    if not found.best:
        return Recommendation(
            id=f"placement:{type_code}:{new_id}",
            scenario="placement",
            priority=0,
            title=f"Места для «{label}» не найдено",
            summary="Ни одна позиция в допустимых зонах не проходит проверку без новых ошибок.",
            changes=[],
            reasons=[Reason(text=f"Проверено позиций: {found.generated}, прошло быстрый фильтр: {found.passed_fast_filter}.")],
            effects=[],
            search=_stats(found),
        )
    best = found.best[0]
    after = summarize(best.plan)
    item = next(e for e in best.plan.equipment if e.id == new_id)
    reasons = [
        Reason(text=f"Объект размещён в зоне «{_zone_label(best.plan, item.zone)}», допустимой для этого типа по справочнику."),
        *(
            [Reason(text=f"Поставить {missed} нельзя: там нет места, где объект не нарушает норм. Показано лучшее место без этого условия.")]
            if missed
            else ([Reason(text=f"Место выбрано {near.words}, как просили.")] if near is not None else [])
        ),
        Reason(text=f"Ошибок проверки после размещения: {after['errors']} (было {before['errors']}); самое узкое место пути посетителя: {after['min_route_width']} м."),
    ]
    if type_code in SERVICE_WINDOW_TYPES and item.operator_position:
        reasons.append(Reason(text="Рабочее место оператора обращено к служебной зоне; проход между окнами не меньше нормы.", source="ВНТП 311-98 «Объекты почтовой связи»", clause="табл. 13, п. 4.5"))
    return Recommendation(
        id=f"placement:{type_code}:{new_id}",
        scenario="placement",
        priority=0,
        title=f"Место для: {label}" + (f" (не {missed}: там нет места)" if missed else ""),
        summary=f"Лучшее место — {_place_words(item.position)}, поворот {item.rotation_deg:g}°. "
        f"Свободная площадь {_metres(before['free_area'])} → {_metres(after['free_area'])} м².",
        changes=[_change_from(plan, best, "add", new_id, type_code)],
        reasons=reasons,
        assumptions=["Из мест, не добавляющих ошибок, выбрано наиболее уместное для типа: окно — у служебной зоны, стеллаж — у стены, терминал — у входа."],
        effects=_effects(before, after, type_code),
        objects=[new_id],
        zones=[item.zone] if item.zone else [],
        alternatives=[_change_from(plan, alt, "add", new_id, type_code) for alt in found.best[1:]],
        search=_stats(found),
    )


class ImprovementStep(BaseModel):
    recommendation: Recommendation
    errors_after: int
    warnings_after: int


class ImprovementReport(BaseModel):
    initial: dict[str, Any]
    final: dict[str, Any]
    steps: list[ImprovementStep]
    improved_plan: dict[str, Any]
    effects: list[Effect]
    elapsed_ms: float
    partial: bool = Field(
        default=False, description="Отведённое время кончилось: показаны найденные шаги"
    )
    notes: list[str] = Field(
        default_factory=list, description="Почему осталось то, что осталось: например, окна кассы не переставляются"
    )


def apply_changes(plan: Plan, changes: list[Change]) -> Plan:
    """Применение изменений рекомендации к плану — то же самое делает редактор."""
    document = plan.model_dump(by_alias=True, exclude_none=True)
    equipment = {e["id"]: e for e in document.get("equipment", [])}
    types = equipment_types(plan)
    for change in changes:
        if change.op == "remove":
            equipment.pop(change.object_id, None)
            continue
        entry = equipment.get(change.object_id) or {
            "id": change.object_id,
            "type": change.equipment_type,
            "label": change.label or (types[change.equipment_type].label if change.equipment_type in types else None),
        }
        if change.op == "add" and change.equipment_type in types and types[change.equipment_type].capacity_persons:
            entry.setdefault("capacity_persons", types[change.equipment_type].capacity_persons)
        for field_name in ("position", "rotation_deg", "size_m", "zone", "operator_position"):
            value = getattr(change, field_name)
            if value is not None:
                entry[field_name] = value
        # Привязка к зоне берётся по новому месту объекта: иначе скамья, уехавшая из зоны
        # ожидания, остаётся к ней привязанной, и план начинает сам себе противоречить.
        if change.zone is None and entry.get("position"):
            landed = core.zone_of_point(plan, tuple(entry["position"]))
            if landed:
                entry["zone"] = landed
            else:
                entry.pop("zone", None)
        equipment[change.object_id] = {k: v for k, v in entry.items() if v is not None}
    document["equipment"] = list(equipment.values())
    return Plan.model_validate(document)


def _score_of(state: dict[str, Any], plan: Plan, reference: Plan) -> objective.Score:
    """Оценка по уже посчитанной сводке: сводка знает всё, кроме величины переноса."""
    return objective.score(
        plan,
        reference,
        errors=state["errors"],
        warnings=state["warnings"],
        entrance_width=state.get("entrance_route_width"),
        narrowest_route=state.get("min_route_width"),
    )


def _step_key(rec: Recommendation) -> str:
    places = ";".join(f"{c.op}:{c.object_id}@{c.position}/{c.rotation_deg}" for c in rec.changes)
    return f"{rec.id}|{places}"


def improve(
    plan: Plan, max_steps: int = 12, fixes_only: bool = False, deadline: Deadline | None = None
) -> ImprovementReport:
    """Последовательное улучшение плана.

    На каждом шаге рекомендации пересчитываются для текущего состояния, и применяется лучшая:
    сначала устранение ошибок, затем пропускная способность, затем комплектность. Так
    рекомендации не конфликтуют между собой — каждая учитывает результат предыдущих.
    fixes_only ограничивает улучшение устранением нарушений: план не пополняется оборудованием.
    """
    started = time.perf_counter()
    deadline = deadline or Deadline()
    initial = summarize(plan)
    current = plan
    steps: list[ImprovementStep] = []
    # Рекомендация запоминается вместе с местом: объект, переставленный раньше, можно передвинуть
    # ещё раз, если на новом месте он создал другое нарушение, — но не на то же место.
    applied: set[str] = set()
    touched: set[str] = set()

    for _ in range(max_steps):
        if steps and deadline.spent():
            break
        state = summarize(current)
        capacity = capacity_model.analyze(current)
        candidates = [
            r
            for r in recommend_fixes(current, state, limit=1, deadline=deadline)
            if _step_key(r) not in applied
        ]
        if not candidates and not fixes_only:
            candidates = [
                r
                for r in recommend_capacity(current, state, capacity, limit=1, deadline=deadline)
                if _step_key(r) not in applied and r.changes
            ]
        if not candidates and not fixes_only:
            candidates = [
                r
                for r in recommend_completeness(current, state, capacity, limit=1, deadline=deadline)
                if _step_key(r) not in applied and r.changes
            ]
        if not candidates:
            break
        chosen = candidates[0]
        next_plan = apply_changes(current, chosen.changes)
        next_state = summarize(next_plan)
        applied.add(_step_key(chosen))
        # Шаг принимается по общей оценке плана, а не только по числу ошибок: расширение
        # прохода с 0,64 до 0,9 м ошибку пока не снимает, но план уже лучше.
        here = _score_of(state, current, plan)
        there = _score_of(next_state, next_plan, plan)
        repeated = chosen.id in touched
        if next_state["errors"] > state["errors"]:
            continue
        if not there.better_than(here, margin=0.5) and not (next_state["errors"] < state["errors"]):
            continue
        if repeated and next_state["errors"] >= state["errors"] and not there.better_than(here, margin=2.0):
            continue
        touched.add(chosen.id)
        steps.append(
            ImprovementStep(
                recommendation=chosen,
                errors_after=next_state["errors"],
                warnings_after=next_state["warnings"],
            )
        )
        current = next_plan

    # Полируется то, что действительно переехало: идентификаторы объектов, а не рекомендаций.
    moved_objects = {c.object_id for step in steps for c in step.recommendation.changes if c.op == "move"}
    current, polish_steps = _polish(plan, current, moved_objects, deadline=deadline)
    steps.extend(polish_steps)

    final = summarize(current)
    return ImprovementReport(
        initial=initial,
        final=final,
        steps=steps,
        improved_plan=current.model_dump(by_alias=True, exclude_none=True),
        effects=_effects(initial, final),
        elapsed_ms=round((time.perf_counter() - started) * 1000, 1),
        partial=deadline.reached,
        notes=[BUILT_IN_LEFT] if _errors_hold_built_in(current) else [],
    )


def _errors_hold_built_in(plan: Plan) -> bool:
    """Осталось ли нарушение, в котором участвует окно кассы: его движок не переставляет."""
    built_in = {e.id for e in plan.equipment if e.type in BUILT_IN_TYPES}
    if not built_in:
        return False
    report = validate(plan)
    return any(
        f.severity is Severity.ERROR and built_in.intersection(f.objects) for f in report.findings
    )


def _polish(
    original: Plan,
    plan: Plan,
    moved: set[str],
    max_steps: int = 4,
    deadline: "Deadline | None" = None,
) -> tuple[Plan, list[ImprovementStep]]:
    """Возврат перенесённых объектов ближе к их прежнему месту, если план от этого выигрывает.

    Устранение нарушений иногда уводит скамью из зоны ожидания или стойку от окон: ошибок нет,
    а пользоваться отделением стало хуже. Здесь каждый перенесённый объект пробуется вернуть в
    ту зону, где он стоял, и изменение принимается только при улучшении общей оценки.
    """
    steps: list[ImprovementStep] = []
    current = plan
    before_zone = {item.id: item.zone for item in original.equipment}
    for object_id in sorted(moved)[:max_steps]:
        if deadline is not None and deadline.spent():
            break
        item = next((e for e in current.equipment if e.id == object_id), None)
        source = next((e for e in original.equipment if e.id == object_id), None)
        if item is None or source is None or item.zone == before_zone.get(object_id):
            continue
        current_score = objective.score(current, original)
        found = search.place(
            current,
            item.type,
            new_id=item.id,
            template=item,
            zone_id=before_zone.get(object_id),
            top_k=FIX_TOP_K,
            keep=1,
            prefer_near=source.position,
        )
        best = next((e for e in found.best if e.errors <= current_score.errors), None)
        if best is None:
            continue
        # Обе оценки считаются от одного исходного плана, иначе величины переноса несравнимы.
        best_score = objective.score(best.plan, original, errors=best.errors, warnings=best.warnings)
        if not best_score.better_than(current_score, margin=0.5):
            continue
        state = summarize(current)
        after = summarize(best.plan)
        change = _change_from(current, best, "move", item.id, item.type, template=item)
        gain = _polish_gain(current_score, best_score)
        recommendation = Recommendation(
            id=f"polish:{item.id}",
            scenario="fix",
            priority=1,
            title=f"Вернуть ближе к прежнему месту: {_object_name(current, item.id)}",
            summary=(
                f"Объект переносится обратно в зону «{_zone_label(best.plan, before_zone.get(object_id))}». "
                + gain
                + f" Ошибок остаётся {after['errors']}."
            ),
            changes=[change],
            reasons=[
                Reason(
                    text=(
                        "Устранение нарушений увело объект из его зоны. Возврат не добавляет ошибок "
                        "и улучшает план по остальным показателям."
                    ),
                    source="Расчёт планировки",
                    clause="сравнение вариантов расстановки",
                )
            ],
            assumptions=[
                "Сравниваются места внутри прежней зоны объекта; принимается только вариант, "
                "который не добавляет ошибок и улучшает общую оценку плана."
            ],
            effects=_effects(state, after),
            objects=[item.id],
            zones=[z for z in [before_zone.get(object_id)] if z],
            search=_stats(found),
        )
        steps.append(
            ImprovementStep(
                recommendation=recommendation,
                errors_after=after["errors"],
                warnings_after=after["warnings"],
            )
        )
        current = best.plan
    return current, steps


def _polish_gain(before: objective.Score, after: objective.Score) -> str:
    """Человеческая формулировка того, что именно улучшилось при возврате объекта."""
    parts: list[str] = []
    if after.waiting_capacity is not None and before.waiting_capacity is not None and after.waiting_capacity > before.waiting_capacity:
        parts.append(f"мест ожидания {before.waiting_capacity} → {after.waiting_capacity}")
    if after.walk_m is not None and before.walk_m is not None and after.walk_m < before.walk_m - 0.05:
        parts.append(f"путь до окон {before.walk_m} → {after.walk_m} м")
    if after.moved_m < before.moved_m - 0.05:
        parts.append(f"перенос {before.moved_m} → {after.moved_m} м")
    if after.warnings < before.warnings:
        parts.append(f"предупреждений {before.warnings} → {after.warnings}")
    return ("Улучшение: " + ", ".join(parts) + ".") if parts else "План становится удобнее."


def recommend_zone_moves(plan: Plan, deadline: "Deadline | None" = None) -> list[Recommendation]:
    """Объекты не в своей зоне по справочнику: перенос в ближайшую допустимую зону.

    Стеллаж посреди клиентского зала проход не перекрывает и ошибкой не считается, но стоит
    не там, где ему положено. Ассистент ищет место в зоне допустимого типа теми же расчётами,
    что и для команды «перенеси в зону», и объясняет перенос ссылкой на справочник.
    """
    types = equipment_types(plan)
    results: list[Recommendation] = []
    for problem in core.misplaced_by_type(plan, {code: t.allowed_zones for code, t in types.items()}):
        if results and deadline is not None and deadline.spent():
            break
        item = next(e for e in plan.equipment if e.id == problem["объект"])
        if item.type in BUILT_IN_TYPES:
            continue
        allowed = [z for z in plan.zones if z.type in problem["допустимые"]]
        if not allowed:
            continue
        target = min(allowed, key=lambda z: core.zone_polygon(z).centroid.distance(ShapelyPoint(item.position)))
        action = language.Action(kind="move", object_id=item.id, zone_id=target.id, text="")
        recommendation = _move_recommendation(plan, action)
        if recommendation is None:
            continue
        type_label = types[item.type].label
        permitted = ", ".join(z.label or z.id for z in allowed)
        recommendation.id = f"zone:move:{item.id}"
        recommendation.priority = 2
        recommendation.title = f"Перенести в «{target.label or target.id}»: {_object_name(plan, item.id)}"
        warnings = next((e for e in recommendation.effects if e.key == "warnings"), None)
        recommendation.summary = f"Новое место в зоне «{target.label or target.id}»."
        if warnings is not None and warnings.before is not None and warnings.after is not None:
            recommendation.summary += (
                f" Предупреждений станет {int(warnings.after)} вместо {int(warnings.before)}, ошибок не прибавится."
            )
        recommendation.reasons = [
            Reason(
                text=(
                    f"По справочнику типов оборудования «{type_label}» ставится только в зонах: "
                    f"{permitted}. Сейчас объект стоит в «{problem['зона'].label or problem['зона'].id}»."
                ),
                source="Справочник типов оборудования плана",
            ),
            *recommendation.reasons,
        ]
        recommendation.assumptions = [
            "Допустимые зоны берутся из справочника плана: изменив справочник, можно разрешить другую зону.",
            *recommendation.assumptions,
        ]
        results.append(recommendation)
    return results


def advise(plan: Plan, deadline: Deadline | None = None) -> AssistantReport:
    """Все рекомендации по плану: сначала нарушения, потом зоны, пропускная способность и состав.

    Семейства считаются по очереди и в порядке важности: если отведённое время кончилось,
    оставшиеся не считаются, а ответ помечается неполным.
    """
    started = time.perf_counter()
    deadline = deadline or Deadline()
    before = summarize(plan)
    capacity = capacity_model.analyze(plan)
    completeness = completeness_model.analyze(plan, capacity)
    recommendations: list[Recommendation] = []
    families = (
        lambda: recommend_fixes(plan, before, deadline=deadline),
        lambda: recommend_zone_moves(plan, deadline=deadline),
        lambda: recommend_capacity(plan, before, capacity, deadline=deadline),
        lambda: recommend_completeness(plan, before, capacity, deadline=deadline),
    )
    for family in families:
        if recommendations and deadline.spent():
            break
        recommendations.extend(family())
    recommendations.sort(key=lambda r: r.priority)
    return AssistantReport(
        recommendations=recommendations,
        capacity=capacity,
        completeness=completeness,
        summary=before,
        elapsed_ms=round((time.perf_counter() - started) * 1000, 1),
        partial=deadline.reached,
    )


class CommandResult(BaseModel):
    """Ответ на команду словами: что поняли, что отвечаем и что предлагаем сделать."""

    understood: list[str] = Field(default_factory=list, description="Понятые действия человеческими словами")
    unknown: list[str] = Field(default_factory=list, description="Части фразы, оставшиеся без ответа")
    source: str = Field(default="разбор фразы", description="Чем разобрана фраза")
    answers: list[str] = Field(default_factory=list, description="Ответы на вопросы о плане")
    recommendations: list[Recommendation] = Field(default_factory=list)
    elapsed_ms: float = 0.0


def _address(plan: Plan, object_id: str) -> str:
    """Короткий адрес объекта — тот же, что в строке адреса редактора: О-07."""
    index = next((i for i, item in enumerate(plan.equipment) if item.id == object_id), None)
    return f"О-{index + 1:02d}" if index is not None else object_id


def _answer(action: language.Action, plan: Plan) -> str:
    """Ответ на вопрос о плане. Считается по плану, ничего в нём не меняя."""
    if action.question == "area":
        state = summarize(plan)
        return (
            f"Площадь помещения {_metres(state['total_area'])} м², занято оборудованием "
            f"{_metres(state['occupied_area'])} м², свободно {_metres(state['free_area'])} м²."
        )
    if action.question == "state":
        state = summarize(plan)
        if not state["errors"] and not state["warnings"]:
            return "Проверка нарушений не нашла: план соответствует нормам, заложенным в сервис."
        return (
            f"Нарушений {state['errors']}, замечаний {state['warnings']}. Разбор каждого — "
            f"во вкладке «Обзор»."
        )
    if action.question == "where":
        if action.object_id:
            item = next((e for e in plan.equipment if e.id == action.object_id), None)
            if item is not None:
                x, y = item.position
                return (
                    f"{item.label or _label(plan, item.type)} ({_address(plan, item.id)}) — зона "
                    f"«{_zone_label(plan, item.zone)}», {_place_words((x, y))} от левого нижнего "
                    f"угла плана."
                )
        if action.equipment_type:
            same = [e for e in plan.equipment if e.type == action.equipment_type]
            if not same:
                return f"{_label(plan, action.equipment_type)} на плане нет."
            where = ", ".join(
                f"{_address(plan, e.id)} в зоне «{_zone_label(plan, e.zone)}»" for e in same[:5]
            )
            tail = f" и ещё {len(same) - 5}" if len(same) > 5 else ""
            return f"{_label(plan, action.equipment_type)}: {where}{tail}."
        return "Не понял, о каком объекте вопрос: назовите тип или адрес вида О-07."
    if action.question == "count":
        if action.equipment_type:
            same = [e for e in plan.equipment if e.type == action.equipment_type]
            if action.zone_id:
                same = [e for e in same if e.zone == action.zone_id]
                where = f" в зоне «{_zone_label(plan, action.zone_id)}»"
            else:
                where = ""
            names = ", ".join(e.label or _address(plan, e.id) for e in same[:5])
            tail = f" и ещё {len(same) - 5}" if len(same) > 5 else ""
            listing = f" — {names}{tail}" if same else ""
            return f"{_label(plan, action.equipment_type)}{where}: {len(same)}{listing}."
        return f"Объектов на плане: {len(plan.equipment)}, зон: {len(plan.zones)}."
    return "Вопрос понят, но ответить на него сервис пока не умеет."


QUESTION_WORDS = {
    "count": "вопрос: сколько",
    "where": "вопрос: где стоит",
    "area": "вопрос: какая площадь",
    "state": "вопрос: что с планом",
}


def _action_words(action: language.Action, plan: Plan) -> str:
    """Понятое действие обратно человеческими словами — чтобы было видно, что сервис понял."""
    label = _label(plan, action.equipment_type) if action.equipment_type else None
    if action.kind == "question":
        asked = QUESTION_WORDS.get(action.question or "", "вопрос о плане")
        return f"{asked}{f': {label}' if label else ''}"
    where = ""
    if action.zone_id:
        where = f" в зону «{_zone_label(plan, action.zone_id)}»"
    elif action.near_entrance:
        where = " у входа"
    elif action.near_partition:
        where = " у перегородки"
    elif action.near_wall:
        where = " у стены"
    if action.kind == "add":
        count = f" × {action.count}" if action.count > 1 else ""
        return f"поставить: {label}{count}{where}"
    if action.kind == "move":
        return f"переставить: {_object_name(plan, action.object_id) if action.object_id else label}{where}"
    if action.kind == "rotate":
        target = _object_name(plan, action.object_id) if action.object_id else label
        return f"развернуть: {target} на {int(action.angle)}°"
    if action.kind == "remove":
        return f"убрать: {_object_name(plan, action.object_id) if action.object_id else label}"
    if action.kind == "fix":
        return "исправить нарушения"
    return "проверить план"


def _remove_recommendation(plan: Plan, object_id: str) -> Recommendation | None:
    item = next((e for e in plan.equipment if e.id == object_id), None)
    if item is None:
        return None
    before = summarize(plan)
    after_plan = apply_changes(plan, [Change(op="remove", object_id=object_id, equipment_type=item.type)])
    after = summarize(after_plan)
    return Recommendation(
        id=f"command:remove:{object_id}",
        scenario="excess",
        priority=0,
        title=f"Убрать: {_object_name(plan, object_id)}",
        summary=(
            f"Объект убирается с плана. Ошибок станет {after['errors']} вместо {before['errors']}, "
            f"свободная площадь {_metres(before['free_area'])} → {_metres(after['free_area'])} м²."
        ),
        changes=[Change(op="remove", object_id=object_id, equipment_type=item.type, label=item.label)],
        reasons=[Reason(text="Действие запрошено командой; проверка показывает, как изменится план.")],
        effects=_effects(before, after, item.type),
        objects=[object_id],
    )


def _rotate_recommendation(plan: Plan, action: language.Action) -> Recommendation | None:
    """Разворот объекта на месте: «разверни кассу на 90 градусов».

    Место остаётся прежним, меняется только поворот, поэтому эффект виден сразу: развёрнутый
    объект перестаёт перекрывать проход — или, наоборот, начинает, и проверка об этом скажет.
    """
    item = _command_target(plan, action)
    if item is None:
        return None
    turned = round((item.rotation_deg + action.angle) % 360, 1)
    change = Change(
        op="move",
        object_id=item.id,
        equipment_type=item.type,
        label=item.label,
        position=item.position,
        rotation_deg=turned,
        previous_position=item.position,
        previous_rotation_deg=item.rotation_deg,
    )
    before = summarize(plan)
    after_plan = apply_changes(plan, [change])
    after = summarize(after_plan)
    degrees = int(action.angle) if float(action.angle).is_integer() else action.angle
    return Recommendation(
        id=f"command:rotate:{item.id}",
        scenario="layout",
        priority=0,
        title=f"Развернуть: {_object_name(plan, item.id)} на {degrees}°",
        summary=(
            f"Поворот с {int(item.rotation_deg)}° на {int(turned)}°, место прежнее. "
            f"Ошибок станет {after['errors']} вместо {before['errors']}."
        ),
        changes=[change],
        reasons=[Reason(text="Действие запрошено командой; проверка показывает, как изменится план.")],
        effects=_effects(before, after, item.type),
        objects=[item.id],
    )


def _ambiguous(plan: Plan, action: language.Action) -> str:
    """Почему команду не выполнили: объект не назван однозначно.

    Ответить «не понял» на понятную фразу нечестно: сервис фразу понял, но не знает, о каком из
    трёх окон речь. Поэтому он говорит, сколько их, и как назвать нужное.
    """
    if action.equipment_type:
        same = [e for e in plan.equipment if e.type == action.equipment_type]
        if len(same) > 1:
            addresses = ", ".join(_address(plan, e.id) for e in same[:6])
            return (
                f"{_label(plan, action.equipment_type)} на плане {len(same)}: {addresses}. "
                f"Назовите нужный по адресу — например «{addresses.split(', ')[0]}»."
            )
        if not same:
            return f"{_label(plan, action.equipment_type)} на плане нет."
    return "Не понял, о каком объекте речь: назовите его по адресу вида О-07."


def _command_target(plan: Plan, action: language.Action) -> Equipment | None:
    """Объект, о котором говорит команда: по адресу, по подписи или единственный своего типа."""
    if action.object_id:
        return next((e for e in plan.equipment if e.id == action.object_id), None)
    if action.equipment_type:
        same = [e for e in plan.equipment if e.type == action.equipment_type]
        if len(same) == 1:
            return same[0]
    return None


def _is_built_in(plan: Plan, action: language.Action) -> bool:
    """Команда про окно кассы: названное по адресу или просто «окно», когда их несколько."""
    target = _command_target(plan, action)
    kind = target.type if target is not None else action.equipment_type
    return kind in BUILT_IN_TYPES


def _move_recommendation(plan: Plan, action: language.Action) -> Recommendation | None:
    object_id = action.object_id
    if object_id is None and action.equipment_type:
        candidates = [e for e in plan.equipment if e.type == action.equipment_type]
        object_id = candidates[0].id if len(candidates) == 1 else None
    if object_id is None:
        return None
    item = next((e for e in plan.equipment if e.id == object_id), None)
    if item is None:
        return None
    before = summarize(plan)
    prefer = passability.entrance_point(plan) if action.near_entrance else item.position
    found = search.place(
        plan,
        item.type,
        new_id=item.id,
        template=item,
        zone_id=action.zone_id,
        top_k=FIX_TOP_K,
        keep=2,
        prefer_near=prefer,
    )
    if not found.best:
        return None
    best = found.best[0]
    after = summarize(best.plan)
    moved = next(e for e in best.plan.equipment if e.id == object_id)
    return Recommendation(
        id=f"command:move:{object_id}",
        scenario="fix",
        priority=0,
        title=f"Переставить: {_object_name(plan, object_id)}",
        summary=(
            f"Новое место в зоне «{_zone_label(best.plan, moved.zone)}». "
            f"Ошибок станет {after['errors']} вместо {before['errors']}."
        ),
        changes=[_change_from(plan, best, "move", object_id, item.type, template=item)],
        reasons=[Reason(text="Место выбрано расчётом среди допустимых: оно не добавляет ошибок проверки.")],
        assumptions=["Из подходящих мест выбрано лучшее по общей оценке плана."],
        effects=_effects(before, after),
        objects=[object_id],
        zones=[moved.zone] if moved.zone else [],
        alternatives=[_change_from(plan, alt, "move", object_id, item.type, template=item) for alt in found.best[1:]],
        search=_stats(found),
    )


def run_command(plan: Plan, text: str) -> CommandResult:
    """Выполняет команду словами: разбирает фразу и готовит рекомендации с предпросмотром.

    Фразу разбирает языковая модель, если она подключена (см. `llm.py`), иначе — словарный
    разбор. Что бы ни разобрало фразу, места и последствия считает один и тот же расчёт, а
    ответ говорит, чем именно она разобрана.
    """
    started = time.perf_counter()
    understanding = llm.understand(text, plan) or language.understand(text, plan)

    result = CommandResult(understood=[], unknown=list(understanding.unknown), source=understanding.source)
    current = plan
    for action in understanding.actions:
        result.understood.append(_action_words(action, plan))
        if action.kind == "question":
            result.answers.append(_answer(action, current))
        elif action.kind == "check":
            result.answers.append(_answer(language.Action(kind="question", question="state"), current))
        elif action.kind == "add" and action.equipment_type:
            for _ in range(min(action.count, 6)):
                recommendation = place_equipment(current, action.equipment_type, action.zone_id, action)
                result.recommendations.append(recommendation)
                if recommendation.changes:
                    current = apply_changes(current, recommendation.changes)
        elif action.kind in ("move", "rotate") and _is_built_in(current, action):
            result.answers.append(BUILT_IN_REFUSAL)
        elif action.kind == "move":
            recommendation = _move_recommendation(current, action)
            if recommendation is not None:
                result.recommendations.append(recommendation)
                current = apply_changes(current, recommendation.changes)
            else:
                result.answers.append(_ambiguous(current, action))
        elif action.kind == "rotate":
            recommendation = _rotate_recommendation(current, action)
            if recommendation is not None:
                result.recommendations.append(recommendation)
                current = apply_changes(current, recommendation.changes)
            else:
                result.answers.append(_ambiguous(current, action))
        elif action.kind == "remove":
            object_id = action.object_id
            if object_id is None and action.equipment_type:
                same = [e for e in current.equipment if e.type == action.equipment_type]
                object_id = same[0].id if len(same) == 1 else None
            recommendation = _remove_recommendation(current, object_id) if object_id else None
            if recommendation is not None:
                result.recommendations.append(recommendation)
                current = apply_changes(current, recommendation.changes)
            else:
                result.answers.append(_ambiguous(current, action))
        elif action.kind == "fix":
            state = summarize(current)
            result.recommendations.extend(recommend_fixes(current, state, limit=3))
    result.elapsed_ms = round((time.perf_counter() - started) * 1000, 1)
    return result

