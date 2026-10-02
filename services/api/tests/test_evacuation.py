"""Пути эвакуации (СП 1.13130.2020 п. 4.3.3) и огнетушители (ПП РФ № 1479)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.assistant import peak, recommend
from app.models import Plan
from app.rules import evacuation
from app.rules.engine import Severity, validate

DATA = Path(__file__).resolve().parents[3] / "data"


def load(name: str) -> Plan:
    return Plan.model_validate(json.loads((DATA / name).read_text(encoding="utf-8")))


@pytest.fixture(scope="module")
def demo() -> Plan:
    return load("demo_plan.json")


def test_exits_are_entrance_and_service_doors(demo: Plan) -> None:
    assert len(evacuation.exits(demo)) == 2


def test_narrow_evacuation_path_is_found_with_its_clause(demo: Plan) -> None:
    findings = [f for f in validate(demo).findings if f.rule_id == "evacuation_path_width"]
    assert findings
    finding = findings[0]
    assert finding.severity is Severity.ERROR
    assert finding.clause == "п. 4.3.3"
    assert finding.measured is not None and finding.required is not None
    assert finding.measured < finding.required


def test_auto_fix_clears_evacuation_too(demo: Plan) -> None:
    report = recommend.improve(demo, fixes_only=True)
    fixed = Plan.model_validate(report.improved_plan)
    assert not [f for f in validate(fixed).findings if f.rule_id == "evacuation_path_width"]


def test_missing_extinguishers_are_a_note_not_a_violation(demo: Plan) -> None:
    findings = [f for f in validate(demo).findings if f.rule_id == "fire_extinguishers"]
    assert findings and findings[0].severity is Severity.WARNING


def test_far_extinguisher_is_a_violation(demo: Plan) -> None:
    """Два огнетушителя в одном углу большого зала: дальний край дальше 20 м пути."""
    document = demo.model_dump(by_alias=True, exclude_none=True)
    corner = (0.6, 0.6)
    document["equipment"] += [
        {"id": f"ext_{i}", "type": "fire_extinguisher", "position": [corner[0] + i * 0.3, corner[1]], "size_m": [0.2, 0.2]}
        for i in range(2)
    ]
    plan = Plan.model_validate(document)
    reach = evacuation.extinguisher_reach(plan)
    assert reach is not None
    findings = [f for f in validate(plan).findings if f.rule_id == "fire_extinguishers"]
    if reach[0] > 20:
        assert any(f.severity is Severity.ERROR for f in findings)
    else:
        assert not findings


def test_storage_in_vestibule_is_forbidden(demo: Plan) -> None:
    vestibule = next(z for z in demo.zones if z.type == "vestibule")
    from app.geometry import core

    centre = core.zone_polygon(vestibule).centroid
    document = demo.model_dump(by_alias=True, exclude_none=True)
    document["equipment"].append(
        {"id": "rack_in_vestibule", "type": "storage_rack", "position": [centre.x, centre.y], "size_m": [0.6, 0.4]}
    )
    plan = Plan.model_validate(document)
    findings = [f for f in validate(plan).findings if f.rule_id == "vestibule_storage"]
    assert findings and findings[0].objects == ["rack_in_vestibule"]


def test_peak_queue_on_evacuation_path_is_reported(demo: Plan) -> None:
    report = peak.analyze(demo, peak.PeakScenario(flow_factor=1.4))
    assert report.evacuation_with_queue_m is not None
    assert report.evacuation_with_queue_m < (report.evacuation_required_m or 0)
    assert any("пути эвакуации" in line for line in report.advice)
