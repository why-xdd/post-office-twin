"""Проверки ассистента: объяснимость, эффект и отсутствие ухудшений."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.assistant import capacity, recommend
from app.geometry import passability
from app.models import Plan
from app.rules.engine import VISITOR_TARGET_TYPES, validate

SAMPLE = Path(__file__).resolve().parents[3] / "data" / "example_plan.json"


@pytest.fixture(scope="module")
def plan() -> Plan:
    return Plan.model_validate(json.loads(SAMPLE.read_text(encoding="utf-8")))


@pytest.fixture(scope="module")
def improvement(plan: Plan) -> recommend.ImprovementReport:
    return recommend.improve(plan)


def test_bottleneck_culprits_are_the_objects_at_the_entrance(plan: Plan) -> None:
    """Сужение у входа создают банкомат и скамья, а не окна, к которым ведёт путь."""
    blockers = passability.bottleneck_blockers(plan, VISITOR_TARGET_TYPES, 1.2)
    culprits = {c for group in blockers.values() for c in group}
    assert {"eq_atm", "eq_bench_1"} <= culprits


def test_path_fix_widens_entrance_to_norm(plan: Plan) -> None:
    rec = recommend.recommend_path_fix(plan, recommend.summarize(plan))
    assert rec is not None
    after = recommend.apply_changes(plan, rec.changes)
    assert (passability.widest_route_width(after, VISITOR_TARGET_TYPES) or 0) >= 1.2
    assert {c.object_id for c in rec.changes} >= {"eq_atm", "eq_bench_1"}


def test_every_recommendation_explains_itself(improvement: recommend.ImprovementReport) -> None:
    assert improvement.steps
    for step in improvement.steps:
        rec = step.recommendation
        assert rec.reasons, rec.id
        assert rec.changes, rec.id
        assert rec.effects, rec.id
        assert all(change.object_id for change in rec.changes)


def test_fix_recommendations_cite_a_norm(improvement: recommend.ImprovementReport) -> None:
    fixes = [s.recommendation for s in improvement.steps if s.recommendation.scenario == "fix"]
    assert fixes
    assert all(any(r.source for r in rec.reasons) for rec in fixes)


def test_improvement_never_adds_errors(improvement: recommend.ImprovementReport) -> None:
    previous = improvement.initial["errors"]
    for step in improvement.steps:
        assert step.errors_after <= previous
        previous = step.errors_after


def test_improved_plan_has_no_errors(improvement: recommend.ImprovementReport) -> None:
    improved = Plan.model_validate(improvement.improved_plan)
    assert validate(improved).errors == 0
    assert improvement.final["min_route_width"] >= 1.2


def test_changes_reproduce_the_improved_plan(plan: Plan, improvement: recommend.ImprovementReport) -> None:
    """Рекомендации применимы в редакторе: последовательное применение даёт тот же план."""
    current = plan
    for step in improvement.steps:
        current = recommend.apply_changes(current, step.recommendation.changes)
    assert validate(current).errors == validate(Plan.model_validate(improvement.improved_plan)).errors


def test_erlang_c_known_value() -> None:
    """Эталон: 2 канала, нагрузка 1 Эрланг — вероятность ожидания 1/3."""
    assert capacity.erlang_c(2, 1.0) == pytest.approx(1 / 3, abs=1e-6)


def test_more_windows_never_increase_wait() -> None:
    assert capacity.windows_needed(60, 3.0) >= capacity.windows_needed(20, 3.0)


def test_place_equipment_does_not_add_errors(plan: Plan) -> None:
    rec = recommend.place_equipment(plan, "storage_rack")
    before = validate(plan).errors
    if rec.changes:
        after = validate(recommend.apply_changes(plan, rec.changes)).errors
        assert after <= before
