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

import time
from typing import Any

from pydantic import BaseModel, Field

from app.assistant import capacity as capacity_model
from app.assistant import completeness as completeness_model
from app.assistant import search
from app.dictionaries import equipment_types
from app.geometry import core, passability
from app.models import Equipment, Plan
from app.rules.engine import SERVICE_WINDOW_TYPES, VISITOR_TARGET_TYPES, Severity, validate

# Объекты, которые разумно двигать первыми: их перенос дешевле перепланировки окон.
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
        eff("errors", "Ошибок проверки", "", True),
        eff("warnings", "Предупреждений", "", True),
        eff("free_area", "Свободная площадь", "м²", False),
        eff("occupied_area", "Занято оборудованием", "м²", True),
        eff("entrance_route_width", "Ширина прохода от входа", "м", False),
        eff("min_route_width", "Самое узкое место пути посетителя", "м", False),
        eff("waiting_capacity", "Вместимость зоны ожидания", "чел.", False),
    ]
    effects[3].improved = None  # занятая площадь сама по себе не хороша и не плоха
    if before.get("equipment_total") == after.get("equipment_total"):
        # При перестановке площадь меняется только из-за снятых наложений и выхода за контур.
        effects[2].improved = None
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
            best_width, best = max(options, key=lambda o: (o[0], -o[1].errors))
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
            "Каждый переставлен на место с наибольшей шириной прохода, не добавляющее ошибок."
        ],
        effects=_effects(before, after),
        objects=[c.object_id for c in changes],
        zones=[c.zone for c in changes if c.zone],
        search=stats,
    )


def recommend_fixes(plan: Plan, before: dict[str, Any], limit: int | None = None) -> list[Recommendation]:
    """Для объектов, участвующих в ошибках, ищется перенос, который уменьшает число ошибок.

    limit останавливает поиск после стольких найденных рекомендаций: при пошаговом улучшении
    применяется одна, и перебирать остальные незачем.
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

    movable = [
        item
        for item in core.floor_equipment(plan)
        if item.id in involved
    ]
    movable.sort(key=lambda i: (MOVE_PRIORITY.get(i.type, 2), -len(involved[i.id])))
    movable = movable[:MAX_FIX_OBJECTS]

    results: list[Recommendation] = [path_fix] if path_fix is not None else []
    for item in movable:
        if limit is not None and len(results) >= limit:
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
        improving = [e for e in found.best if e.errors < before["errors"]]
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
) -> list[Recommendation]:
    results: list[Recommendation] = []
    working = plan
    for issue in completeness_model.analyze(plan, capacity):
        if limit is not None and sum(1 for r in results if r.changes) >= limit:
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
                    f"Освобождается {round((after['free_area'] or 0) - (before['free_area'] or 0), 2)} м².",
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
            f"({c.position[0]:.2f}, {c.position[1]:.2f}) в зоне «{_zone_label(candidate_plan, c.zone)}»"
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
                f"{round((before['free_area'] or 0) - (after['free_area'] or 0), 2)} м².",
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
) -> list[Recommendation]:
    results: list[Recommendation] = []
    if not capacity.available:
        return results
    for group in capacity.groups:
        if limit is not None and sum(1 for r in results if r.changes) >= limit:
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
                    id=f"capacity:{type_code}",
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
                id=f"capacity:{type_code}",
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


def place_equipment(plan: Plan, type_code: str, zone_id: str | None = None) -> Recommendation:
    """Сценарий «найти место для дополнительного рабочего места, окна или стеллажа»."""
    before = summarize(plan)
    new_id = _unique_id(plan, type_code)
    label = _label(plan, type_code)
    found = search.place(plan, type_code, new_id=new_id, zone_id=zone_id, keep=3)
    if not found.best:
        return Recommendation(
            id=f"placement:{type_code}",
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
        Reason(text=f"Ошибок проверки после размещения: {after['errors']} (было {before['errors']}); самое узкое место пути посетителя: {after['min_route_width']} м."),
    ]
    if type_code in SERVICE_WINDOW_TYPES and item.operator_position:
        reasons.append(Reason(text="Рабочее место оператора обращено к служебной зоне; проход между окнами не меньше нормы.", source="ВНТП 311-98 «Объекты почтовой связи»", clause="табл. 13, п. 4.5"))
    return Recommendation(
        id=f"placement:{type_code}",
        scenario="placement",
        priority=0,
        title=f"Место для: {label}",
        summary=f"Лучшее место — ({item.position[0]:.2f}, {item.position[1]:.2f}), поворот {item.rotation_deg:g}°. "
        f"Свободная площадь {before['free_area']} → {after['free_area']} м².",
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
        equipment[change.object_id] = {k: v for k, v in entry.items() if v is not None}
    document["equipment"] = list(equipment.values())
    return Plan.model_validate(document)


def improve(plan: Plan, max_steps: int = 12) -> ImprovementReport:
    """Последовательное улучшение плана.

    На каждом шаге рекомендации пересчитываются для текущего состояния, и применяется лучшая:
    сначала устранение ошибок, затем пропускная способность, затем комплектность. Так
    рекомендации не конфликтуют между собой — каждая учитывает результат предыдущих.
    """
    started = time.perf_counter()
    initial = summarize(plan)
    current = plan
    steps: list[ImprovementStep] = []
    applied: set[str] = set()

    for _ in range(max_steps):
        state = summarize(current)
        capacity = capacity_model.analyze(current)
        candidates = [r for r in recommend_fixes(current, state, limit=1) if r.id not in applied]
        if not candidates:
            candidates = [
                r for r in recommend_capacity(current, state, capacity, limit=1) if r.id not in applied and r.changes
            ]
        if not candidates:
            candidates = [
                r for r in recommend_completeness(current, state, capacity, limit=1) if r.id not in applied and r.changes
            ]
        if not candidates:
            break
        chosen = candidates[0]
        next_plan = apply_changes(current, chosen.changes)
        next_state = summarize(next_plan)
        if next_state["errors"] > state["errors"]:
            applied.add(chosen.id)
            continue
        applied.add(chosen.id)
        steps.append(
            ImprovementStep(
                recommendation=chosen,
                errors_after=next_state["errors"],
                warnings_after=next_state["warnings"],
            )
        )
        current = next_plan

    final = summarize(current)
    return ImprovementReport(
        initial=initial,
        final=final,
        steps=steps,
        improved_plan=current.model_dump(by_alias=True, exclude_none=True),
        effects=_effects(initial, final),
        elapsed_ms=round((time.perf_counter() - started) * 1000, 1),
    )


def advise(plan: Plan) -> AssistantReport:
    started = time.perf_counter()
    before = summarize(plan)
    capacity = capacity_model.analyze(plan)
    completeness = completeness_model.analyze(plan, capacity)
    recommendations = [
        *recommend_fixes(plan, before),
        *recommend_capacity(plan, before, capacity),
        *recommend_completeness(plan, before, capacity),
    ]
    recommendations.sort(key=lambda r: r.priority)
    return AssistantReport(
        recommendations=recommendations,
        capacity=capacity,
        completeness=completeness,
        summary=before,
        elapsed_ms=round((time.perf_counter() - started) * 1000, 1),
    )
