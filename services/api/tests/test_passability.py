"""Проверки проходимости на планах с заранее известной шириной прохода."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.geometry import passability
from app.models import Plan

SAMPLE = Path(__file__).resolve().parents[3] / "data" / "example_plan.json"


def room(width: float, height: float, equipment: list[dict], door_x: float = 0.5) -> Plan:
    return Plan.model_validate(
        {
            "ops": {"id": "t"},
            "walls": [
                {"id": "s", "from": [0, 0], "to": [width, 0], "type": "outer"},
                {"id": "e", "from": [width, 0], "to": [width, height], "type": "outer"},
                {"id": "n", "from": [width, height], "to": [0, height], "type": "outer"},
                {"id": "w", "from": [0, height], "to": [0, 0], "type": "outer"},
            ],
            "doors": [
                {
                    "id": "d",
                    "wall": [door_x - 0.5, height, door_x + 0.5, height],
                    "type": "entrance",
                    "width_m": 1.0,
                }
            ],
            "equipment": equipment,
        }
    )


def test_open_room_is_wide() -> None:
    plan = room(
        8,
        6,
        [
            {
                "id": "cash",
                "type": "cashier_window",
                "position": [4, 0.5],
                "size_m": [1.2, 0.8],
                "operator_position": [4, 0.1],
            }
        ],
        door_x=4,
    )
    [route] = passability.client_route_widths(plan, ("cashier_window",))
    assert route.reachable
    assert route.width_m > 3.0


def test_corridor_of_known_width_is_measured() -> None:
    """Стеллаж перегораживает помещение, оставляя проход ровно 1,0 м у восточной стены."""
    plan = room(
        8,
        6,
        [
            {"id": "wall_rack", "type": "storage_rack", "position": [3.5, 3], "size_m": [7, 0.5]},
            {
                "id": "cash",
                "type": "cashier_window",
                "position": [4, 0.5],
                "size_m": [1.2, 0.8],
                "operator_position": [4, 0.1],
            },
        ],
        door_x=1,
    )
    [route] = passability.client_route_widths(plan, ("cashier_window",))
    assert route.reachable
    assert route.width_m == pytest.approx(1.0, abs=0.03)


def test_fully_blocked_target_is_unreachable() -> None:
    plan = room(
        8,
        6,
        [
            {"id": "barrier", "type": "storage_rack", "position": [4, 3], "size_m": [8, 0.5]},
            {
                "id": "cash",
                "type": "cashier_window",
                "position": [4, 0.5],
                "size_m": [1.2, 0.8],
                "operator_position": [4, 0.1],
            },
        ],
        door_x=1,
    )
    [route] = passability.client_route_widths(plan, ("cashier_window",))
    assert not route.reachable


def test_sample_plan_entrance_is_narrow() -> None:
    """В примере заказчика банкомат и скамья оставляют у входа проход около 0,65 м."""
    plan = Plan.model_validate(json.loads(SAMPLE.read_text(encoding="utf-8")))
    routes = passability.client_route_widths(plan, ("cashier_window", "pvz_counter"))
    assert routes
    assert all(r.reachable for r in routes)
    assert max(r.width_m for r in routes) < 0.7


def test_largest_clear_circle_in_open_room() -> None:
    plan = room(8, 6, [], door_x=4)
    plan = Plan.model_validate(
        {
            **plan.model_dump(by_alias=True),
            "zones": [
                {
                    "id": "z",
                    "type": "client_area",
                    "polygon": [[0, 0], [8, 0], [8, 6], [0, 6]],
                }
            ],
        }
    )
    diameter, center = passability.largest_clear_circle(plan, ("client_area",))
    assert diameter == pytest.approx(6.0, abs=0.03)
    assert center is not None
