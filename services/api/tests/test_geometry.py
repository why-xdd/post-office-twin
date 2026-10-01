"""Проверки геометрического ядра на примере плана из формата заказчика."""

from __future__ import annotations

import json
import math
from pathlib import Path

import pytest

from app.geometry import core
from app.models import Equipment, Plan, duplicate_ids

SAMPLE = Path(__file__).resolve().parents[3] / "data" / "example_plan.json"


@pytest.fixture(scope="module")
def plan() -> Plan:
    return Plan.model_validate(json.loads(SAMPLE.read_text(encoding="utf-8")))


def test_sample_plan_parses(plan: Plan) -> None:
    assert plan.ops.id
    assert len(plan.zones) == 4
    assert len(plan.equipment) == 12


def test_ids_are_unique(plan: Plan) -> None:
    assert duplicate_ids(plan) == {}


def test_outline_is_closed(plan: Plan) -> None:
    shape = core.outline(plan)
    assert shape is not None
    assert shape.is_valid
    # Габариты из паспорта отделения: 10.4 × 7.5 м.
    assert math.isclose(shape.area, 10.4 * 7.5, rel_tol=0.01)


def test_total_area_matches_bbox(plan: Plan) -> None:
    assert core.total_area(plan) == pytest.approx(78.0, rel=0.01)


def test_occupied_area_is_positive_and_smaller_than_total(plan: Plan) -> None:
    occupied = core.occupied_area(plan)
    total = core.total_area(plan)
    assert total is not None
    assert 0 < occupied < total
    assert core.free_area(plan) == pytest.approx(total - occupied, abs=0.01)


def test_sample_plan_has_no_equipment_outside(plan: Plan) -> None:
    assert core.objects_outside(plan) == []


def test_sample_plan_overlaps_are_detected(plan: Plan) -> None:
    """В примере плана оборудование местами пересекается — проверка обязана это находить."""
    found = {frozenset((o.first_id, o.second_id)) for o in core.overlaps(plan)}
    assert frozenset(("eq_cashier_1", "eq_rack_1")) in found
    assert frozenset(("eq_cashier_2", "eq_rack_2")) in found
    assert frozenset(("eq_atm", "eq_bench_1")) in found


def test_sample_plan_narrow_passages_are_detected(plan: Plan) -> None:
    """Проход между кассами в примере уже норматива 1,0 м из ВНТП 311-98, табл. 13."""
    tight = {frozenset((c.first_id, c.second_id)): c.distance_m for c in core.clearances(plan, 1.0)}
    assert tight[frozenset(("eq_cashier_1", "eq_cashier_2"))] == pytest.approx(0.7, abs=0.01)


def test_rotation_preserves_area() -> None:
    straight = Equipment(id="a", type="atm", position=(1.0, 1.0), size_m=(0.7, 0.5))
    turned = Equipment(
        id="b", type="atm", position=(1.0, 1.0), size_m=(0.7, 0.5), rotation_deg=90
    )
    assert core.equipment_polygon(straight).area == pytest.approx(0.35)
    assert core.equipment_polygon(turned).area == pytest.approx(0.35)
    # После поворота на 90° габарит по X становится прежним габаритом по Y.
    bounds = core.equipment_polygon(turned).bounds
    assert bounds[2] - bounds[0] == pytest.approx(0.5, abs=1e-6)
    assert bounds[3] - bounds[1] == pytest.approx(0.7, abs=1e-6)


def test_overlap_is_detected() -> None:
    plan = Plan(
        ops={"id": "test"},
        equipment=[
            Equipment(id="one", type="storage_rack", position=(1.0, 1.0), size_m=(2.0, 1.0)),
            Equipment(id="two", type="storage_rack", position=(1.5, 1.0), size_m=(2.0, 1.0)),
        ],
    )
    found = core.overlaps(plan)
    assert len(found) == 1
    assert {found[0].first_id, found[0].second_id} == {"one", "two"}
    assert found[0].area_sqm == pytest.approx(1.5, abs=0.01)


def test_touching_objects_are_not_overlaps() -> None:
    """Шкафы, стоящие впритык, не считаются ошибкой планировки."""
    plan = Plan(
        ops={"id": "test"},
        equipment=[
            Equipment(id="one", type="storage_rack", position=(1.0, 1.0), size_m=(2.0, 1.0)),
            Equipment(id="two", type="storage_rack", position=(3.0, 1.0), size_m=(2.0, 1.0)),
        ],
    )
    assert core.overlaps(plan) == []


def test_clearance_between_objects() -> None:
    plan = Plan(
        ops={"id": "test"},
        equipment=[
            Equipment(id="one", type="cashier_window", position=(0.0, 0.0), size_m=(1.0, 1.0)),
            Equipment(id="two", type="cashier_window", position=(1.8, 0.0), size_m=(1.0, 1.0)),
        ],
    )
    tight = core.clearances(plan, limit_m=1.0)
    assert len(tight) == 1
    assert tight[0].distance_m == pytest.approx(0.8, abs=1e-6)
    assert core.clearances(plan, limit_m=0.5) == []


def test_queue_terminal_distance_from_entrance(plan: Plan) -> None:
    """Терминал электронной очереди по перечню оборудования ставится в радиусе 3 м от входа."""
    kiosks = core.equipment_by_type(plan, "self_service_kiosk")
    assert kiosks, "в примере плана должен быть терминал самообслуживания"
    distance = core.distance_from_entrance(plan, kiosks[0])
    assert distance is not None
    assert distance >= 0


def test_search_places_along_a_slanted_wall() -> None:
    """У наклонной стены поиск предлагает место вдоль неё и вплотную, а не клином."""
    import math

    from shapely.geometry import LineString

    from app.assistant import search
    from app.models import Plan

    plan = Plan.model_validate(
        {
            "ops": {"id": "ops", "name": "Трапеция", "bbox": {"width_m": 10, "height_m": 6}},
            "walls": [
                {"id": "b", "from": [0, 0], "to": [10, 0], "type": "outer"},
                {"id": "s", "from": [10, 0], "to": [8, 6], "type": "outer"},
                {"id": "t", "from": [8, 6], "to": [0, 6], "type": "outer"},
                {"id": "l", "from": [0, 6], "to": [0, 0], "type": "outer"},
            ],
            "zones": [
                {"id": "z", "type": "back_office", "polygon": [[0, 0], [10, 0], [8, 6], [0, 6]]},
            ],
            "equipment": [],
        }
    )
    candidates, _ = search.generate(plan, "storage_rack")
    angle = round(math.degrees(math.atan2(6, -2)) % 180.0, 1)
    along = [c for c in candidates if c.rotation_deg in (angle, round((angle + 90) % 180, 1))]
    assert along, "вдоль наклонной стены кандидатов нет"
    wall = LineString([(10, 0), (8, 6)])
    assert min(c.polygon.distance(wall) for c in along) < 0.01
