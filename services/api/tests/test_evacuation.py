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


def hall(partition_door: bool | None, extinguishers: list[tuple[float, float]], exits: bool = True) -> Plan:
    """Зал 30 × 10 м. partition_door: None — без перегородки, True — перегородка x=15 с дверью
    вверху, False — глухая перегородка. Вход слева сверху."""
    walls = [
        {"id": "s", "from": [0, 0], "to": [30, 0], "type": "outer"},
        {"id": "e", "from": [30, 0], "to": [30, 10], "type": "outer"},
        {"id": "n", "from": [30, 10], "to": [0, 10], "type": "outer"},
        {"id": "w", "from": [0, 10], "to": [0, 0], "type": "outer"},
    ]
    doors = [{"id": "d_main", "wall": [1, 10, 2.5, 10], "type": "entrance", "width_m": 1.5}] if exits else []
    if partition_door is not None:
        walls.append({"id": "p", "from": [15.125, 0], "to": [15.125, 10], "type": "partition"})
        if partition_door:
            doors.append({"id": "d_in", "wall": [15.125, 8.9, 15.125, 9.9], "type": "internal", "width_m": 1.0})
    equipment = [
        {"id": f"ext_{i}", "type": "fire_extinguisher", "position": list(point), "size_m": [0.2, 0.2]}
        for i, point in enumerate(extinguishers)
    ]
    return Plan.model_validate({"ops": {"id": "hall"}, "walls": walls, "doors": doors, "equipment": equipment})


def extinguisher_findings(plan: Plan) -> list:
    return [f for f in validate(plan).findings if f.rule_id == "fire_extinguishers"]


def test_extinguisher_path_does_not_go_through_a_partition() -> None:
    """Огнетушители за перегородкой: путь обходит её через дверь, а не идёт сквозь стену."""
    behind = [(15.4, 0.5), (15.4, 1.0)]
    open_hall = evacuation.extinguisher_reach(hall(None, behind))
    walled = evacuation.extinguisher_reach(hall(True, behind))
    assert open_hall is not None and walled is not None
    assert walled.farthest_m > open_hall.farthest_m + 5
    assert any(f.severity is Severity.ERROR and "п. 406" in f.message for f in extinguisher_findings(hall(True, behind)))


def test_room_without_a_path_to_an_extinguisher_is_a_violation() -> None:
    """Глухая перегородка: из половины зала к огнетушителю нет пути — это нарушение, а не тишина."""
    reach = evacuation.extinguisher_reach(hall(False, [(15.4, 0.5), (15.4, 1.0)]))
    assert reach is not None and reach.unreachable_sqm > 100
    assert any("нет пути" in f.message for f in extinguisher_findings(hall(False, [(15.4, 0.5), (15.4, 1.0)])))


def test_extinguishers_spread_over_the_hall_pass() -> None:
    plan = hall(True, [(7.5, 0.3), (22.5, 0.3)])
    assert not [f for f in extinguisher_findings(plan) if f.severity is Severity.ERROR]


def test_plan_without_an_exit_fails_evacuation() -> None:
    findings = [f for f in validate(hall(None, [], exits=False)).findings if f.rule_id == "evacuation_path_width"]
    assert findings and "нет выхода наружу" in findings[0].message


def test_plan_without_zones_is_still_checked() -> None:
    """Без зон эвакуация проверяется по всему помещению: стеллаж поперёк зала перекрывает путь."""
    document = hall(None, []).model_dump(by_alias=True, exclude_none=True)
    document["equipment"].append({"id": "wall_rack", "type": "storage_rack", "position": [10, 5.15], "size_m": [0.6, 9.7]})
    findings = [f for f in validate(Plan.model_validate(document)).findings if f.rule_id == "evacuation_path_width"]
    assert findings and "Помещение" in findings[0].message


def test_evacuation_names_where_it_is_narrow_and_what_to_move(demo: Plan) -> None:
    """Где узко — объекты на концах мерки; что переставить — то, без чего путь наружу шире."""
    from shapely.geometry import Point

    from app.geometry import core, passability

    neck = evacuation.bottlenecks(demo)[0]
    assert neck.gap is not None
    (ax, ay), (bx, by) = neck.gap
    assert abs(((ax - bx) ** 2 + (ay - by) ** 2) ** 0.5 - neck.measured_m) <= 0.05
    for owner in neck.pinch_objects:
        shape = core.equipment_polygon(next(e for e in demo.equipment if e.id == owner))
        assert min(shape.distance(Point(ax, ay)), shape.distance(Point(bx, by))) < 0.02
    assert neck.culprits, "у демо-плана путь освобождается перестановкой"
    assert all(next(e for e in demo.equipment if e.id == c).type not in evacuation.BUILT_IN_TYPES for c in neck.culprits)
    probe = Point(neck.point)
    cache = passability.erosion(demo, exclude=set(neck.culprits))
    wider = max(cache.width(probe, Point(e))[0] for e in evacuation.exits(demo))
    assert wider > neck.measured_m
    finding = next(f for f in validate(demo).findings if f.rule_id == "evacuation_path_width")
    assert "Самое узкое место" in finding.message and "Освободить путь" in finding.message


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
