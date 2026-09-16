"""Применение правил к плану.

Каждая находка содержит правило-основание, затронутые объекты и измеренное значение рядом с
нормативным. Формулировка собирается из этих данных, поэтому её всегда можно проверить.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from app.geometry import core
from app.models import Plan
from app.rules.catalog import (
    COVERAGE_PER_WINDOW,
    DEFAULT_RULES,
    RURAL_COVERAGE_PER_WINDOW,
    Category,
    Rule,
    Severity,
)

SERVICE_WINDOW_TYPES = ("cashier_window", "pvz_counter")
WAITING_ZONE_TYPES = ("waiting", "queue_accumulator")
CLIENT_ZONE_TYPES = ("client_area",)


class Finding(BaseModel):
    """Одно срабатывание правила."""

    rule_id: str
    title: str
    severity: Severity
    category: Category
    source: str
    clause: str
    message: str = Field(description="Формулировка с измеренным и нормативным значением")
    objects: list[str] = Field(default_factory=list, description="Объекты, к которым относится")
    measured: float | None = None
    required: float | None = None
    unit: str = ""


class ValidationReport(BaseModel):
    findings: list[Finding]
    errors: int
    warnings: int
    infos: int
    checked_rules: list[str]
    skipped_rules: list[str] = Field(default_factory=list)


def _enabled(rules: list[Rule], overrides: dict[str, bool] | None) -> list[Rule]:
    overrides = overrides or {}
    return [r for r in rules if overrides.get(r.id, r.enabled)]


def _finding(rule: Rule, message: str, objects: list[str], measured: float | None) -> Finding:
    return Finding(
        rule_id=rule.id,
        title=rule.title,
        severity=rule.severity,
        category=rule.category,
        source=rule.source,
        clause=rule.clause,
        message=message,
        objects=objects,
        measured=measured,
        required=rule.value,
        unit=rule.unit,
    )


def _check_passages(plan: Plan, rule: Rule) -> list[Finding]:
    if rule.value is None:
        return []
    windows = {item.id for item in core.equipment_by_type(plan, *SERVICE_WINDOW_TYPES)}
    findings: list[Finding] = []
    for gap in core.clearances(plan, rule.value):
        # Норматив прохода между рабочими местами применяется к местам обслуживания.
        if gap.first_id not in windows and gap.second_id not in windows:
            continue
        findings.append(
            _finding(
                rule,
                f"Проход {gap.distance_m} м между «{gap.first_id}» и «{gap.second_id}» "
                f"меньше требуемых {rule.value} м.",
                [gap.first_id, gap.second_id],
                gap.distance_m,
            )
        )
    return findings


def _check_wall_clearance(plan: Plan, rule: Rule) -> list[Finding]:
    """Проход от стены проверяется только для объектов с рабочей зоной.

    Норматив задаёт проход от стены до границы рабочей зоны работника. Банкомат, стенд,
    скамья, сейф и стеллаж прислоняются к стене по своему назначению, и расстояние до стены
    для них нарушением не является.
    """
    if rule.value is None:
        return []
    with_workspace = {
        item.id for item in plan.equipment if item.operator_position is not None
    }
    if not with_workspace:
        return []
    return [
        _finding(
            rule,
            f"Рабочее место «{gap.first_id}» отстоит от стены «{gap.second_id}» "
            f"на {gap.distance_m} м при требуемых {rule.value} м.",
            [gap.first_id, gap.second_id],
            gap.distance_m,
        )
        for gap in core.wall_clearances(plan, rule.value)
        if gap.first_id in with_workspace
    ]


def _check_client_area(plan: Plan, rule: Rule) -> list[Finding]:
    if rule.value is None:
        return []
    windows = core.equipment_by_type(plan, *SERVICE_WINDOW_TYPES)
    if not windows:
        return []
    client_zones = core.zone_by_type(plan, *CLIENT_ZONE_TYPES, *WAITING_ZONE_TYPES)
    if not client_zones:
        return []
    available = sum(core.area_of_zone(zone) for zone in client_zones)
    required = rule.value * len(windows)
    if available >= required:
        return []
    return [
        _finding(
            rule,
            f"Клиентская зона {round(available, 2)} м² при {len(windows)} окнах обслуживания; "
            f"норматив требует не менее {round(required, 2)} м² "
            f"({rule.value} м² на каждое операционное место).",
            [zone.id for zone in client_zones],
            round(available, 2),
        )
    ]


def _check_counter_height(plan: Plan, rule: Rule) -> list[Finding]:
    if rule.value is None:
        return []
    findings: list[Finding] = []
    for item in core.equipment_by_type(plan, *SERVICE_WINDOW_TYPES):
        height = item.counter_height_m
        if height is None:
            continue
        if height > rule.value:
            findings.append(
                _finding(
                    rule,
                    f"Низ окна обслуживания «{item.id}» на высоте {height} м, "
                    f"допускается не более {rule.value} м.",
                    [item.id],
                    height,
                )
            )
    return findings


def _check_queue_terminal(plan: Plan, rule: Rule) -> list[Finding]:
    if rule.value is None:
        return []
    findings: list[Finding] = []
    for item in core.equipment_by_type(plan, "self_service_kiosk"):
        distance = core.distance_from_entrance(plan, item)
        if distance is None:
            continue
        if distance > rule.value:
            findings.append(
                _finding(
                    rule,
                    f"Терминал очереди «{item.id}» удалён от входа на {distance} м, "
                    f"по перечню оборудования — не более {rule.value} м.",
                    [item.id],
                    distance,
                )
            )
    return findings


def _check_coverage(plan: Plan, rule: Rule) -> list[Finding]:
    population = plan.ops.settlement_population
    if population is None:
        return []
    windows = core.equipment_by_type(plan, *SERVICE_WINDOW_TYPES)
    if not windows:
        return []
    if plan.ops.type and "rural" in plan.ops.type:
        per_window = RURAL_COVERAGE_PER_WINDOW
    else:
        per_window = next(
            (high for threshold, _, high in COVERAGE_PER_WINDOW if population >= threshold),
            RURAL_COVERAGE_PER_WINDOW,
        )
    capacity = per_window * len(windows)
    message = (
        f"Отделение с {len(windows)} окнами обслуживания рассчитано на зону охвата "
        f"{capacity} тыс. человек ({per_window} тыс. на окно). "
        f"Население населённого пункта — {population} тыс."
    )
    if capacity < population:
        needed = int(-(-population // per_window))
        message += (
            f" Одно такое отделение покрывает часть пункта; на весь пункт по нормативу "
            f"требуется суммарно не менее {needed} окон обслуживания в сети отделений."
        )
    return [_finding(rule, message, [item.id for item in windows], capacity)]


CHECKS = {
    "passage_between_workplaces": _check_passages,
    "clearance_from_wall": _check_wall_clearance,
    "client_area_per_workplace": _check_client_area,
    "counter_height": _check_counter_height,
    "queue_terminal_near_entrance": _check_queue_terminal,
    "coverage_per_service_window": _check_coverage,
}


def structural_findings(plan: Plan) -> list[Finding]:
    """Проверки целостности плана: идентификаторы, границы, пересечения, связи с зонами."""
    findings: list[Finding] = []
    base = dict(
        severity=Severity.ERROR,
        category=Category.GEOMETRY,
        source="Требования задачи",
        clause="Проверка корректности",
    )

    from app.models import duplicate_ids

    for section, ids in duplicate_ids(plan).items():
        findings.append(
            Finding(
                rule_id="unique_ids",
                title="Уникальность идентификаторов",
                message=f"Повторяющиеся идентификаторы в разделе «{section}»: {', '.join(ids)}.",
                objects=ids,
                **base,
            )
        )

    outside = core.objects_outside(plan)
    if outside:
        findings.append(
            Finding(
                rule_id="objects_inside_outline",
                title="Объекты за пределами помещения",
                message=f"За границами помещения находятся объекты: {', '.join(outside)}.",
                objects=outside,
                **base,
            )
        )

    for overlap in core.overlaps(plan):
        findings.append(
            Finding(
                rule_id="no_overlaps",
                title="Пересечение физических объектов",
                message=(
                    f"Объекты «{overlap.first_id}» и «{overlap.second_id}» пересекаются "
                    f"на {overlap.area_sqm} м²."
                ),
                objects=[overlap.first_id, overlap.second_id],
                measured=overlap.area_sqm,
                unit="м²",
                **base,
            )
        )

    for problem in core.mismatched_zone_links(plan):
        declared = problem["указана"] or "не указана"
        actual = problem["фактически"] or "вне зон"
        findings.append(
            Finding(
                rule_id="zone_links",
                title="Связь объекта с зоной",
                message=(
                    f"У объекта «{problem['объект']}» указана зона «{declared}», "
                    f"а фактически он расположен в «{actual}»."
                ),
                objects=[str(problem["объект"])],
                **{**base, "severity": Severity.WARNING},
            )
        )

    return findings


def validate(plan: Plan, overrides: dict[str, bool] | None = None) -> ValidationReport:
    """Полная проверка плана: целостность плюс нормативные правила."""
    findings = structural_findings(plan)
    active = _enabled(DEFAULT_RULES, overrides)
    checked: list[str] = []
    skipped: list[str] = []

    for rule in active:
        check = CHECKS.get(rule.id)
        if check is None:
            skipped.append(rule.id)
            continue
        checked.append(rule.id)
        findings.extend(check(plan, rule))

    return ValidationReport(
        findings=findings,
        errors=sum(1 for f in findings if f.severity is Severity.ERROR),
        warnings=sum(1 for f in findings if f.severity is Severity.WARNING),
        infos=sum(1 for f in findings if f.severity is Severity.INFO),
        checked_rules=checked,
        skipped_rules=skipped,
    )
