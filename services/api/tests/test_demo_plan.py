"""Демонстрационное отделение: открывается, проверяется и исправляется ассистентом."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.assistant import recommend
from app.dictionaries import equipment_types
from app.models import Plan
from app.rules.engine import validate

DEMO = Path(__file__).resolve().parents[3] / "data" / "demo_plan.json"


@pytest.fixture(scope="module")
def plan() -> Plan:
    return Plan.model_validate(json.loads(DEMO.read_text(encoding="utf-8")))


def test_demo_plan_is_complete(plan: Plan) -> None:
    ids = [e.id for e in plan.equipment]
    assert len(ids) == len(set(ids)), "идентификаторы объектов уникальны"
    assert {z.type for z in plan.zones} >= {"client_area", "waiting", "queue_accumulator", "back_office", "staff_room"}
    # Свой тип оборудования в справочнике плана — справочники расширяются без изменения кода.
    assert "sorting_table" in equipment_types(plan)
    assert any(e.type == "sorting_table" for e in plan.equipment)


def test_demo_plan_has_problems_to_fix(plan: Plan) -> None:
    report = validate(plan, {})
    rules = {f.rule_id for f in report.findings if f.severity == "error"}
    assert {"no_overlaps", "movement_path_width"} <= rules


def test_assistant_fixes_demo_plan(plan: Plan) -> None:
    result = recommend.improve(plan)
    assert result.initial["errors"] > 0
    assert result.final["errors"] == 0
    fixed = Plan.model_validate(result.improved_plan)
    assert validate(fixed, {}).errors == 0


def test_object_in_wrong_zone_is_found_and_moved(plan: Plan) -> None:
    """Стеллаж в клиентском зале: проверка замечает, ассистент переносит в служебную зону."""
    report = validate(plan, {})
    flagged = [f for f in report.findings if f.rule_id == "zone_allowed_for_type"]
    assert any("eq_rack_hall" in f.objects for f in flagged)

    moves = recommend.recommend_zone_moves(plan)
    move = next(r for r in moves if r.objects == ["eq_rack_hall"])
    assert move.reasons[0].source == "Справочник типов оборудования плана"
    moved = recommend.apply_changes(plan, move.changes)
    assert not any(
        f.rule_id == "zone_allowed_for_type" and "eq_rack_hall" in f.objects for f in validate(moved, {}).findings
    )
    assert validate(moved, {}).errors <= report.errors
