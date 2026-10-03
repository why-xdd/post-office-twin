"""Сравнение вариантов и смета: цены задаёт человек, без цены итог помечается неполным."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app import compare
from app.assistant import recommend
from app.models import Plan

DATA = Path(__file__).resolve().parents[3] / "data"


@pytest.fixture(scope="module")
def pair() -> tuple[Plan, Plan]:
    before = Plan.model_validate(json.loads((DATA / "demo_plan.json").read_text(encoding="utf-8")))
    after = Plan.model_validate(recommend.improve(before, fixes_only=True).improved_plan)
    return before, after


def test_same_plan_has_no_changes(pair: tuple[Plan, Plan]) -> None:
    before, _ = pair
    result = compare.compare(before, before, compare.Prices())
    assert not result.added and not result.removed and not result.moved and not result.lines
    assert result.total == 0 and result.complete


def test_auto_fix_is_priced_by_given_rates(pair: tuple[Plan, Plan]) -> None:
    before, after = pair
    result = compare.compare(before, after, compare.Prices(move=1500))
    assert result.moved
    line = next(l for l in result.lines if l.key == "move")
    assert line.total == 1500 * len(result.moved)
    errors = next(m for m in result.metrics if m.key == "errors")
    assert errors.after < errors.before and errors.better is True


def test_missing_prices_are_not_invented(pair: tuple[Plan, Plan]) -> None:
    before, _ = pair
    document = before.model_dump(by_alias=True, exclude_none=True)
    document["equipment"].append({"id": "new_kiosk", "type": "self_service_kiosk", "position": [3, 3], "size_m": [0.6, 0.6]})
    after = Plan.model_validate(document)
    result = compare.compare(before, after, compare.Prices(install=2000))
    buy = next(l for l in result.lines if l.key == "equipment:self_service_kiosk")
    assert buy.price is None and buy.total is None
    assert not result.complete and buy.text in result.missing
    assert result.total == 2000
    priced = compare.compare(before, after, compare.Prices(install=2000, equipment={"self_service_kiosk": 350000}))
    assert priced.complete and priced.total == 352000


def test_moved_partition_is_measured(pair: tuple[Plan, Plan]) -> None:
    before, _ = pair
    document = before.model_dump(by_alias=True, exclude_none=True)
    wall = next(w for w in document["walls"] if w.get("type") == "partition")
    wall["from"] = [wall["from"][0] + 0.5, wall["from"][1]]
    wall["to"] = [wall["to"][0] + 0.5, wall["to"][1]]
    after = Plan.model_validate(document)
    result = compare.compare(before, after, compare.Prices(wall_per_m=4000))
    assert result.walls_changed_m > 0
    line = next(l for l in result.lines if l.key == "wall_per_m")
    assert line.total == pytest.approx(4000 * result.walls_changed_m)


def test_demolished_partitions_are_priced(pair: tuple[Plan, Plan]) -> None:
    before, _ = pair
    document = before.model_dump(by_alias=True, exclude_none=True)
    document["walls"] = [w for w in document["walls"] if w.get("type") != "partition"]
    after = Plan.model_validate(document)
    result = compare.compare(before, after, compare.Prices(wall_removal_per_m=1000))
    assert result.walls_removed_m > 0
    line = next(l for l in result.lines if l.key == "wall_removal_per_m")
    assert line.total == pytest.approx(1000 * result.walls_removed_m)


def test_prices_must_be_finite_and_not_negative() -> None:
    from pydantic import ValidationError

    for bad in ({"move": -1}, {"equipment": {"atm": -49000}}, {"install": float("inf")}):
        with pytest.raises(ValidationError):
            compare.Prices.model_validate(bad)
