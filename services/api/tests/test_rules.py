"""Проверки движка правил: срабатывания, отсутствие ложных срабатываний, отключение правил."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.models import Plan
from app.rules.catalog import Severity
from app.rules.engine import validate

SAMPLE = Path(__file__).resolve().parents[3] / "data" / "example_plan.json"


@pytest.fixture(scope="module")
def sample_plan() -> Plan:
    return Plan.model_validate(json.loads(SAMPLE.read_text(encoding="utf-8")))


def rule_ids(plan: Plan, overrides: dict[str, bool] | None = None) -> list[str]:
    return [f.rule_id for f in validate(plan, overrides).findings]


def test_every_finding_cites_its_source(sample_plan: Plan) -> None:
    """Вывод проверки без ссылки на основание бесполезен — такого быть не должно."""
    for finding in validate(sample_plan).findings:
        assert finding.source
        assert finding.clause


def test_overlaps_are_errors(sample_plan: Plan) -> None:
    overlaps = [f for f in validate(sample_plan).findings if f.rule_id == "no_overlaps"]
    assert len(overlaps) == 3
    assert all(f.severity is Severity.ERROR for f in overlaps)


def test_narrow_passage_between_service_windows(sample_plan: Plan) -> None:
    passages = [
        f for f in validate(sample_plan).findings if f.rule_id == "passage_between_workplaces"
    ]
    assert passages
    assert any("0.7" in f.message for f in passages)


def test_wall_clearance_ignores_furniture_against_walls() -> None:
    """Банкомат и скамья стоят вплотную к стене по назначению — это не нарушение."""
    plan = Plan.model_validate(
        {
            "ops": {"id": "t"},
            "walls": [
                {"id": "w_s", "from": [0, 0], "to": [6, 0], "type": "outer"},
                {"id": "w_e", "from": [6, 0], "to": [6, 5], "type": "outer"},
                {"id": "w_n", "from": [6, 5], "to": [0, 5], "type": "outer"},
                {"id": "w_w", "from": [0, 5], "to": [0, 0], "type": "outer"},
            ],
            "equipment": [
                {"id": "atm", "type": "atm", "position": [0.3, 2.5], "size_m": [0.5, 0.7]},
                {
                    "id": "bench",
                    "type": "waiting_seat",
                    "position": [3.0, 0.3],
                    "size_m": [2.0, 0.5],
                },
            ],
        }
    )
    assert "clearance_from_wall" not in rule_ids(plan)


def test_wall_clearance_applies_to_operator_workplace() -> None:
    """У рабочего места оператора проход от стены проверяется."""
    plan = Plan.model_validate(
        {
            "ops": {"id": "t"},
            "walls": [
                {"id": "w_s", "from": [0, 0], "to": [6, 0], "type": "outer"},
                {"id": "w_e", "from": [6, 0], "to": [6, 5], "type": "outer"},
                {"id": "w_n", "from": [6, 5], "to": [0, 5], "type": "outer"},
                {"id": "w_w", "from": [0, 5], "to": [0, 0], "type": "outer"},
            ],
            "equipment": [
                {
                    "id": "cash",
                    "type": "cashier_window",
                    "position": [3.0, 0.5],
                    "size_m": [1.2, 0.8],
                    "operator_position": [3.0, 1.2],
                }
            ],
        }
    )
    assert "clearance_from_wall" in rule_ids(plan)


def test_queue_terminal_too_far_from_entrance() -> None:
    plan = Plan.model_validate(
        {
            "ops": {"id": "t"},
            "walls": [
                {"id": "w_s", "from": [0, 0], "to": [10, 0], "type": "outer"},
                {"id": "w_e", "from": [10, 0], "to": [10, 6], "type": "outer"},
                {"id": "w_n", "from": [10, 6], "to": [0, 6], "type": "outer"},
                {"id": "w_w", "from": [0, 6], "to": [0, 0], "type": "outer"},
            ],
            "doors": [
                {
                    "id": "d_main",
                    "wall": [0, 6, 1.5, 6],
                    "type": "entrance",
                    "width_m": 1.5,
                }
            ],
            "equipment": [
                {
                    "id": "kiosk",
                    "type": "self_service_kiosk",
                    "position": [9.0, 1.0],
                    "size_m": [0.6, 0.6],
                }
            ],
        }
    )
    findings = [f for f in validate(plan).findings if f.rule_id == "queue_terminal_near_entrance"]
    assert len(findings) == 1
    assert findings[0].measured is not None and findings[0].measured > 3.0


def test_rule_can_be_disabled(sample_plan: Plan) -> None:
    """Правило отключается без изменения кода — это требование расширяемости."""
    assert "passage_between_workplaces" in rule_ids(sample_plan)
    assert "passage_between_workplaces" not in rule_ids(
        sample_plan, {"passage_between_workplaces": False}
    )


def test_coverage_rule_needs_population(sample_plan: Plan) -> None:
    """Без данных о населении правило охвата молчит, а не выдумывает значение."""
    assert "coverage_per_service_window" not in rule_ids(sample_plan)

    document = sample_plan.model_dump(by_alias=True)
    document["ops"]["settlement_population"] = 80
    with_population = Plan.model_validate(document)
    assert "coverage_per_service_window" in rule_ids(with_population)


def test_report_counters_match_findings(sample_plan: Plan) -> None:
    report = validate(sample_plan)
    assert report.errors == sum(1 for f in report.findings if f.severity is Severity.ERROR)
    assert report.warnings == sum(1 for f in report.findings if f.severity is Severity.WARNING)
    assert report.infos == sum(1 for f in report.findings if f.severity is Severity.INFO)


def test_operator_seat_in_doorway_blocks_the_door(sample_plan: Plan) -> None:
    """Касса у перегородки допустима, пока стул оператора не стоит в проёме служебной двери."""
    plan = sample_plan.model_copy(deep=True)
    cashier = next(e for e in plan.equipment if e.id == "eq_cashier_2")
    assert "doors_not_blocked" not in rule_ids(plan)

    # Дверь в перегородке x = 7,5 на участке y 4–5: оператор садится вплотную к проёму.
    cashier.position = (6.125, 4.5)
    cashier.rotation_deg = 90.0
    cashier.operator_position = (6.925, 4.5)
    blocked = [f for f in validate(plan).findings if f.rule_id == "doors_not_blocked"]
    assert blocked and blocked[0].objects == ["eq_cashier_2"]
    assert blocked[0].severity is Severity.ERROR


def test_narrow_path_is_one_finding_per_severity(sample_plan: Plan) -> None:
    """Узкий проход — одна проблема плана: иначе счётчик нарушений скачет без причины."""
    findings = [f for f in validate(sample_plan).findings if f.rule_id == "movement_path_width"]
    assert len(findings) == 1
    assert findings[0].severity is Severity.ERROR
    # В находке перечислены все цели, до которых путь узок, и виновники сужения.
    assert "eq_cashier_1" in findings[0].message
    assert findings[0].objects
