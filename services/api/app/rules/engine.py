"""Применение правил к плану.

Каждая находка содержит правило-основание, затронутые объекты и измеренное значение рядом с
нормативным. Формулировка собирается из этих данных, поэтому её всегда можно проверить.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from shapely.geometry import Point as ShapelyPoint
from shapely.geometry import box

from app.dictionaries import equipment_types, zone_types
from app.geometry import core, passability
from app.models import Plan
from app.rules.catalog import (
    CORRESPONDENCE_WORKPLACE_SQM,
    COVERAGE_PER_WINDOW,
    DEFAULT_RULES,
    MIN_PATH_WITH_POCKETS_M,
    PARCEL_WORKPLACE_SQM,
    RURAL_COVERAGE_PER_WINDOW,
    Category,
    Rule,
    Severity,
)

SERVICE_WINDOW_TYPES = ("cashier_window", "pvz_counter")
WAITING_ZONE_TYPES = ("waiting", "queue_accumulator")
CLIENT_ZONE_TYPES = ("client_area",)
STAFF_ZONE_TYPES = ("back_office",)
# Объекты, к которым посетитель должен иметь возможность подойти.
VISITOR_TARGET_TYPES = (*SERVICE_WINDOW_TYPES, "self_service_kiosk", "atm", "package_drop_box")


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
    skipped_reasons: dict[str, str] = Field(
        default_factory=dict, description="Почему правило не удалось применить к этому плану"
    )


def _enabled(rules: list[Rule], overrides: dict[str, bool] | None) -> list[Rule]:
    overrides = overrides or {}
    return [r for r in rules if overrides.get(r.id, r.enabled)]


def _finding(
    rule: Rule,
    message: str,
    objects: list[str],
    measured: float | None,
    severity: Severity | None = None,
    required: float | None = None,
) -> Finding:
    return Finding(
        rule_id=rule.id,
        title=rule.title,
        severity=severity or rule.severity,
        category=rule.category,
        source=rule.source,
        clause=rule.clause,
        message=message,
        objects=objects,
        measured=measured,
        required=required if required is not None else rule.value,
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


def _check_path_width(plan: Plan, rule: Rule, explain: bool = True) -> list[Finding]:
    """Фактическая ширина пути от входа до мест обслуживания посетителей.

    explain=False пропускает поиск виновников сужения: он нужен человеку, а не счётчику ошибок
    при переборе кандидатов в поиске мест.
    """
    if rule.value is None:
        return []
    unreachable: list[str] = []
    # Узкий проход — одна проблема плана, а не отдельная на каждый маршрут: иначе число
    # нарушений скачет от того, как именно разошлись ширины у разных окон.
    narrow: dict[str, float] = {}
    for route in passability.client_route_widths(plan, VISITOR_TARGET_TYPES):
        if not route.reachable:
            unreachable.append(route.target_id)
        elif route.width_m < rule.value:
            narrow[route.target_id] = route.width_m

    findings: list[Finding] = []
    if unreachable:
        findings.append(
            _finding(
                rule,
                "От входа нельзя дойти до: "
                + ", ".join(f"«{t}»" for t in unreachable)
                + " — путь перекрыт оборудованием.",
                unreachable,
                0.0,
                severity=Severity.ERROR,
            )
        )
    if not narrow:
        return findings

    # Находка ссылается на объекты, создающие сужение: переставлять нужно их, а не цели маршрута.
    blockers = (
        passability.bottleneck_blockers(plan, VISITOR_TARGET_TYPES, MIN_PATH_WITH_POCKETS_M)
        if explain
        else {}
    )
    for limit, severity in ((MIN_PATH_WITH_POCKETS_M, Severity.ERROR), (rule.value, Severity.WARNING)):
        group = {target: width for target, width in narrow.items() if width < limit}
        if not group:
            continue
        for target in group:
            narrow.pop(target, None)
        width = min(group.values())
        names = ", ".join(f"«{t}»" for t in sorted(group))
        culprits: list[str] = []
        for candidates in blockers.values():
            for candidate in candidates:
                if candidate not in culprits:
                    culprits.append(candidate)
        cause = f" Сужение создают: {', '.join(f'«{c}»' for c in culprits)}." if culprits else ""
        if severity is Severity.ERROR:
            message = (
                f"Путь от входа сужается до {width} м — к {names}. Норма {rule.value} м, даже с "
                f"карманами для разъезда — не менее {MIN_PATH_WITH_POCKETS_M} м: посетитель на "
                f"кресле-коляске не пройдёт.{cause}"
            )
        else:
            message = (
                f"Путь от входа сужается до {width} м — к {names}. Это допустимо только при "
                f"карманах для разъезда длиной не менее 2 м.{cause}"
            )
        findings.append(_finding(rule, message, culprits or sorted(group), width, severity=severity))
    return findings


def _check_turning_circle(plan: Plan, rule: Rule) -> list[Finding]:
    if rule.value is None:
        return []
    zone_types = (*CLIENT_ZONE_TYPES, *WAITING_ZONE_TYPES)
    if not core.zone_by_type(plan, *zone_types):
        return []
    diameter, _center = passability.largest_clear_circle(plan, zone_types)
    if diameter >= rule.value:
        return []
    return [
        _finding(
            rule,
            f"В клиентской зоне нет свободного места для разворота кресла-коляски: наибольший "
            f"свободный круг — {diameter} м при требуемых {rule.value} м.",
            [zone.id for zone in core.zone_by_type(plan, *zone_types)],
            diameter,
        )
    ]


def _check_counter_front(plan: Plan, rule: Rule) -> list[Finding]:
    """Рабочий фронт — сторона окна, обращённая к посетителю, то есть длинная сторона габарита."""
    if rule.value is None:
        return []
    findings: list[Finding] = []
    for item in core.equipment_by_type(plan, *SERVICE_WINDOW_TYPES):
        front = max(item.size_m)
        if front < rule.value:
            findings.append(
                _finding(
                    rule,
                    f"Рабочий фронт «{item.id}» — {front} м, требуется не менее {rule.value} м.",
                    [item.id],
                    front,
                )
            )
    return findings


def _check_door_maneuver(plan: Plan, rule: Rule) -> list[Finding]:
    """Свободная площадка перед главным входом с внутренней стороны помещения."""
    if rule.value is None:
        return []
    entrance = passability.entrance_point(plan)
    shape = core.outline(plan)
    if entrance is None or shape is None:
        return []
    half = rule.value / 2.0
    # Квадрат строится от точки входа внутрь помещения, туда, где больше площади.
    ex, ey = entrance
    candidates = [
        box(ex - half, ey - rule.value, ex + half, ey),
        box(ex - half, ey, ex + half, ey + rule.value),
        box(ex - rule.value, ey - half, ex, ey + half),
        box(ex, ey - half, ex + rule.value, ey + half),
    ]
    square = max(candidates, key=lambda c: c.intersection(shape).area)
    blockers = [
        item.id
        for item in plan.equipment
        if core.equipment_polygon(item).intersection(square).area > 0.01
    ]
    if not blockers:
        return []
    return [
        _finding(
            rule,
            f"Площадка {rule.value} × {rule.value} м перед входом занята: "
            + ", ".join(f"«{b}»" for b in blockers)
            + ".",
            blockers,
            None,
        )
    ]


def _check_workplace_area(plan: Plan, rule: Rule) -> list[Finding]:
    parcel = core.equipment_by_type(plan, "pvz_counter")
    correspondence = core.equipment_by_type(plan, "cashier_window")
    staff_zones = core.zone_by_type(plan, *STAFF_ZONE_TYPES)
    if not (parcel or correspondence) or not staff_zones:
        return []
    required = len(parcel) * PARCEL_WORKPLACE_SQM + len(correspondence) * CORRESPONDENCE_WORKPLACE_SQM
    available = round(sum(core.area_of_zone(z) for z in staff_zones), 2)
    if available >= required:
        return []
    return [
        _finding(
            rule,
            f"Служебная зона {available} м² при потребности {required} м²: "
            f"{len(parcel)} мест приёма посылок по {PARCEL_WORKPLACE_SQM} м² и "
            f"{len(correspondence)} мест корреспонденции по {CORRESPONDENCE_WORKPLACE_SQM} м².",
            [item.id for item in (*parcel, *correspondence)],
            available,
            required=required,
        )
    ]


CHECKS = {
    "passage_between_workplaces": _check_passages,
    "clearance_from_wall": _check_wall_clearance,
    "client_area_per_workplace": _check_client_area,
    "counter_height": _check_counter_height,
    "queue_terminal_near_entrance": _check_queue_terminal,
    "coverage_per_service_window": _check_coverage,
    "movement_path_width": _check_path_width,
    "wheelchair_turning_circle": _check_turning_circle,
    "counter_front_width": _check_counter_front,
    "door_maneuver_space": _check_door_maneuver,
    "workplace_area": _check_workplace_area,
}

SKIP_REASONS = {
    "ramp_width": "В формате плана нет объекта «пандус» и наружной площадки входа — "
    "проверять нечем. Правило применится, когда пандус будет добавлен в справочник.",
}


def structural_findings(plan: Plan) -> list[Finding]:
    """Проверки целостности плана: идентификаторы, границы, пересечения, связи с зонами."""
    findings: list[Finding] = []
    base = dict(
        severity=Severity.ERROR,
        category=Category.GEOMETRY,
        source="Проверка целостности плана",
        clause="данные и геометрия плана",
    )

    from app.models import duplicate_ids

    for section, ids in duplicate_ids(plan).items():
        findings.append(
            Finding(
                rule_id="unique_ids",
                title="Уникальность идентификаторов",
                message=(
                    f"Повторяющиеся идентификаторы в разделе «{section}»: "
                    + ", ".join(f"«{i}»" for i in ids)
                    + "."
                ),
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
                message=(
                    "За границами помещения находятся объекты: "
                    + ", ".join(f"«{o}»" for o in outside)
                    + "."
                ),
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

    labels = {door.id: door.label or door.id for door in plan.doors}
    for door_id, blockers in core.blocked_doors(plan):
        findings.append(
            Finding(
                rule_id="doors_not_blocked",
                title="Проём двери не загорожен",
                message=(
                    f"Проход через «{labels[door_id]}» перекрыт: "
                    f"{', '.join(f'«{b}»' for b in blockers)} — через дверь не пройти."
                ),
                objects=blockers,
                **base,
            )
        )

    names = {item.id: item.label or item.id for item in plan.equipment}
    for owner_id, blocker_id in core.operator_space_conflicts(plan):
        findings.append(
            Finding(
                rule_id="operator_space_free",
                title="Место сотрудника занято",
                message=(
                    f"«{names[blocker_id]}» стоит на месте сотрудника у «{names[owner_id]}»: "
                    "работать за стойкой невозможно."
                ),
                objects=[blocker_id, owner_id],
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

    types = equipment_types(plan)
    zone_labels = {code: z.label for code, z in zone_types(plan).items()}
    for problem in core.misplaced_by_type(plan, {code: t.allowed_zones for code, t in types.items()}):
        item_id = str(problem["объект"])
        item = next(e for e in plan.equipment if e.id == item_id)
        zone = problem["зона"]
        type_label = types[item.type].label if item.type in types else item.type
        permitted = ", ".join(zone_labels.get(code, code).lower() for code in problem["допустимые"])
        findings.append(
            Finding(
                rule_id="zone_allowed_for_type",
                title="Объект не в своей зоне",
                message=(
                    f"«{item.label or item_id}» ({type_label.lower()}) стоит в зоне «{zone.label or zone.id}», "
                    f"а по справочнику этому типу место в: {permitted}."
                ),
                objects=[item_id],
                **{**base, "severity": Severity.WARNING},
            )
        )

    return findings


def validate(
    plan: Plan, overrides: dict[str, bool] | None = None, explain: bool = True
) -> ValidationReport:
    """Полная проверка плана: целостность плюс нормативные правила.

    explain=False отключает дорогие поясняющие расчёты, не влияющие на число находок.
    """
    findings = structural_findings(plan)
    active = _enabled(DEFAULT_RULES, overrides)
    checked: list[str] = []
    skipped: list[str] = []
    reasons: dict[str, str] = {}

    for rule in active:
        check = CHECKS.get(rule.id)
        if check is None:
            skipped.append(rule.id)
            reasons[rule.id] = SKIP_REASONS.get(rule.id, "Проверка для правила не реализована.")
            continue
        checked.append(rule.id)
        if check is _check_path_width:
            findings.extend(_check_path_width(plan, rule, explain=explain))
        else:
            findings.extend(check(plan, rule))

    return ValidationReport(
        findings=findings,
        errors=sum(1 for f in findings if f.severity is Severity.ERROR),
        warnings=sum(1 for f in findings if f.severity is Severity.WARNING),
        infos=sum(1 for f in findings if f.severity is Severity.INFO),
        checked_rules=checked,
        skipped_rules=skipped,
        skipped_reasons=reasons,
    )
