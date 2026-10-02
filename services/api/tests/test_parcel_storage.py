"""Ёмкость хранения посылок по ВНТП 311-98 п. 3.10."""

from __future__ import annotations

import json
from pathlib import Path

from app.assistant import storage
from app.models import Plan
from app.rules.engine import validate

DATA = Path(__file__).resolve().parents[3] / "data"


def demo(**ops: float) -> Plan:
    document = json.loads((DATA / "demo_plan.json").read_text(encoding="utf-8"))
    document["ops"].update(ops)
    return Plan.model_validate(document)


def test_norm_is_two_days_in_and_sixty_percent_out() -> None:
    report = storage.analyze(demo(parcels_in_per_day=90, parcels_out_per_day=50))
    assert report.required == 2 * 90 + 30
    assert report.enough


def test_double_delivery_runs_out_of_places() -> None:
    report = storage.analyze(demo(parcels_in_per_day=90, parcels_out_per_day=50), factor=2.0)
    assert not report.enough and report.deficit == report.required - report.places
    assert "Не хватает" in report.advice[0]


def test_letters_rack_holds_no_parcels() -> None:
    labels = [i.label for i in storage.analyze(demo(parcels_in_per_day=90)).items]
    assert not any("писем" in label for label in labels)


def test_rule_only_applies_when_volumes_are_known() -> None:
    unknown = demo()
    unknown.ops.parcels_in_per_day = None
    assert not [f for f in validate(unknown).findings if f.rule_id == "parcel_storage_capacity"]
    short = demo(parcels_in_per_day=200)
    finding = next(f for f in validate(short).findings if f.rule_id == "parcel_storage_capacity")
    assert finding.clause == "п. 3.10" and finding.measured < finding.required
