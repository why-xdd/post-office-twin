"""Сеть отделений: какие ремонтировать первыми.

Почте нужно выбирать: из 38 тысяч отделений ремонт нужен 24 тысячам, а за год обновляют сотни.
Экран «Сеть» сводит все сохранённые планы в одну очередь на ремонт. Порядок прозрачен: у каждого
отделения видно, из чего сложился балл, а веса перечислены в WEIGHTS и показываются в интерфейсе.

Что весит больше: то, что угрожает людям (пути эвакуации, доступность для маломобильных), и то,
что каждый день видят посетители (нехватка окон в пик). Меньше — прочие нормы и сроки осмотров.
"""

from __future__ import annotations

from datetime import date
from typing import Any, Callable

from pydantic import BaseModel, Field

from app.assistant import peak
from app.models import Plan
from app.operations import Incident, inspections_due
from app.rules.catalog import Category
from app.rules.engine import Severity, ValidationReport

WEIGHTS = {
    "fire": 5.0,
    "accessibility": 4.0,
    "windows": 4.0,
    "other": 1.0,
    "out_of_service": 2.0,
    "overdue": 0.5,
}
WEIGHT_LABELS = {
    "fire": "нарушение пожарной безопасности",
    "accessibility": "нарушение доступности для маломобильных",
    "windows": "окно, которого не хватает в пик",
    "other": "прочее нарушение норм",
    "out_of_service": "неработающий объект по обращению",
    "overdue": "просроченный осмотр",
}


class Factor(BaseModel):
    key: str
    count: int
    points: float
    text: str


class Office(BaseModel):
    plan_id: str
    title: str
    area_sqm: float | None
    daily_visitors: int | None
    score: float
    rank: int = 0
    factors: list[Factor]
    errors: int
    worst_wait_min: float | None = Field(default=None, description="Самое долгое среднее ожидание в пик; None — растёт без предела")
    queue_unbounded: bool = False
    failed: str | None = None


class NetworkReport(BaseModel):
    offices: list[Office]
    weights: list[dict[str, Any]]
    total_errors: int
    offices_with_fire: int
    offices_with_accessibility: int
    windows_short: int


def _factor(key: str, count: int, text: str) -> Factor | None:
    if count <= 0:
        return None
    return Factor(key=key, count=count, points=round(WEIGHTS[key] * count, 1), text=text)


def assess(
    plan_id: str,
    title: str,
    plan: Plan,
    report: ValidationReport,
    incidents: list[Incident],
    today: date | None = None,
) -> Office:
    today = today or date.today()
    errors = [f for f in report.findings if f.severity is Severity.ERROR]
    fire = sum(1 for f in errors if f.category is Category.FIRE)
    access = sum(1 for f in errors if f.category is Category.ACCESSIBILITY)
    other = len(errors) - fire - access

    broken = sorted({i.object_id for i in incidents if i.out_of_service and i.status != "resolved"})
    known = {e.id for e in plan.equipment}
    peak_report = peak.analyze(plan, peak.PeakScenario(out_of_service=[b for b in broken if b in known]))
    short = 0
    worst: float | None = None
    unbounded = False
    if peak_report.available:
        for group in peak_report.groups:
            short += max(0, group.windows_needed - len(group.windows))
            if group.mean_wait_min is None:
                unbounded = True
            elif worst is None or group.mean_wait_min > worst:
                worst = group.mean_wait_min
    overdue = sum(1 for due in inspections_due(plan, today) if due.days < 0)

    factors = [
        _factor("fire", fire, f"пожарная безопасность: {fire}"),
        _factor("accessibility", access, f"доступность для МГН: {access}"),
        _factor("windows", short, f"в пик не хватает окон: {short}"),
        _factor("other", other, f"прочие нормы: {other}"),
        _factor("out_of_service", len(broken), f"не работает по обращениям: {len(broken)}"),
        _factor("overdue", overdue, f"просрочено осмотров: {overdue}"),
    ]
    present = [f for f in factors if f is not None]
    hints = plan.flow_hints
    return Office(
        plan_id=plan_id,
        title=title or plan.ops.name or plan.ops.id,
        area_sqm=plan.ops.total_area_sqm,
        daily_visitors=hints.avg_daily_visitors if hints else None,
        score=round(sum(f.points for f in present), 1),
        factors=present,
        errors=len(errors),
        worst_wait_min=None if unbounded else (round(worst, 1) if worst is not None else None),
        queue_unbounded=unbounded,
    )


def build(
    plans: list[dict[str, Any]],
    load: Callable[[str], tuple[Plan, ValidationReport]],
    incidents: Callable[[str], list[Incident]],
) -> NetworkReport:
    """plans — список из хранилища; load отдаёт план и его проверку (через кэш)."""
    offices: list[Office] = []
    for summary in plans:
        try:
            plan, report = load(summary["id"])
            offices.append(assess(summary["id"], summary.get("title") or "", plan, report, incidents(summary["id"])))
        except Exception as error:  # noqa: BLE001 — один битый план не должен ронять всю сеть
            offices.append(
                Office(
                    plan_id=summary["id"],
                    title=summary.get("title") or summary["id"],
                    area_sqm=None,
                    daily_visitors=None,
                    score=0,
                    factors=[],
                    errors=0,
                    failed=f"План не разобран: {error.__class__.__name__}",
                )
            )
    # При равном балле первым — отделение с большим потоком: ремонт там коснётся больше людей.
    offices.sort(key=lambda o: (o.failed is not None, -o.score, -(o.daily_visitors or 0)))
    for index, office in enumerate(offices, start=1):
        office.rank = index
    return NetworkReport(
        offices=offices,
        weights=[{"key": k, "weight": w, "label": WEIGHT_LABELS[k]} for k, w in WEIGHTS.items()],
        total_errors=sum(o.errors for o in offices),
        offices_with_fire=sum(1 for o in offices if any(f.key == "fire" for f in o.factors)),
        offices_with_accessibility=sum(1 for o in offices if any(f.key == "accessibility" for f in o.factors)),
        windows_short=sum(f.count for o in offices for f in o.factors if f.key == "windows"),
    )
