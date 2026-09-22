"""Обмен с 1С: выгрузка описи и сверка плана с ведомостью основных средств."""

from __future__ import annotations

import csv
import importlib
import io
import json
from pathlib import Path
from typing import Iterator

import pytest
from fastapi.testclient import TestClient

from app import exchange
from app.models import Plan

DATA = Path(__file__).resolve().parents[3] / "data"
DEMO = DATA / "demo_plan.json"
LEDGER = DATA / "1c_inventory_sample.csv"


@pytest.fixture(scope="module")
def plan() -> Plan:
    return Plan.model_validate(json.loads(DEMO.read_text(encoding="utf-8")))


@pytest.fixture()
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    monkeypatch.setenv("OPS_TWIN_DB", str(tmp_path / "plans.sqlite"))
    import app.main as main

    importlib.reload(main)
    with TestClient(main.app) as test_client:
        yield test_client


def test_export_lists_every_object_with_its_numbers(plan: Plan) -> None:
    text = exchange.export_csv(plan).decode("utf-8-sig")
    rows = list(csv.reader(io.StringIO(text), delimiter=";"))
    assert tuple(rows[0]) == exchange.EXPORT_COLUMNS
    assert len(rows) - 1 == len(plan.equipment)
    window = next(r for r in rows if r[6] == "eq_win_1")
    assert window[0] == "ЗР-000001" and window[2] == "ЗР-ОК-0101" and window[5] == "Операционный зал"


def test_export_reads_back_as_a_perfect_match(plan: Plan) -> None:
    """Своя же выгрузка, загруженная обратно, сверяется без единого расхождения."""
    rows, _ = exchange.read_accounting(exchange.decode(exchange.export_csv(plan)))
    result = exchange.reconcile(plan, rows)
    assert not result.missing_on_plan and not result.not_in_accounting
    assert result.location_mismatches == 0 and result.serial_mismatches == 0


def test_sample_ledger_finds_every_planted_discrepancy(plan: Plan) -> None:
    """В демонстрационной ведомости заложено по одному расхождению каждого вида."""
    rows, recognised = exchange.read_accounting(exchange.decode(LEDGER.read_bytes()))
    assert recognised["inventory_number"] == "Инвентарный номер"
    assert recognised["serial_number"] == "Заводской номер"
    result = exchange.reconcile(plan, rows, recognised)
    assert [row.inventory_number for row in result.missing_on_plan] == ["ЗР-000099"]
    assert [item.object_id for item in result.not_in_accounting] == ["eq_display"]
    assert [m.object_id for m in result.matched if m.location_differs] == ["eq_rack_hall"]
    assert [m.object_id for m in result.matched if m.serial_differs] == ["eq_scale"]
    assert result.rows_read == 26


def test_numbers_match_regardless_of_spelling(plan: Plan) -> None:
    ledger = "Инв. номер;Наименование\nзр 000001;Окно\n"
    rows, _ = exchange.read_accounting(ledger)
    result = exchange.reconcile(plan, rows)
    assert [m.object_id for m in result.matched] == ["eq_win_1"]


def test_file_without_number_column_is_rejected() -> None:
    with pytest.raises(ValueError, match="Инвентарный номер"):
        exchange.read_accounting("Наименование;Количество\nСтол;1\n")


def test_api_round_trip(client: TestClient) -> None:
    document = json.loads(DEMO.read_text(encoding="utf-8"))
    exported = client.post("/api/exchange/1c/export", json={"document": document})
    assert exported.status_code == 200
    assert "attachment" in exported.headers["content-disposition"]
    checked = client.post(
        "/api/exchange/1c/reconcile",
        data={"document": json.dumps(document, ensure_ascii=False)},
        files={"file": ("ведомость.csv", LEDGER.read_bytes(), "text/csv")},
    )
    assert checked.status_code == 200, checked.text
    body = checked.json()
    assert body["location_mismatches"] == 1 and len(body["missing_on_plan"]) == 1
    bad = client.post(
        "/api/exchange/1c/reconcile",
        data={"document": json.dumps(document)},
        files={"file": ("x.csv", "Наименование\nСтол\n".encode(), "text/csv")},
    )
    assert bad.status_code == 422


def test_comma_inside_values_does_not_confuse_the_delimiter(plan: Plan) -> None:
    ledger = "Инвентарный номер;Наименование, модель\nЗР-000001;Стол, офисный\nЗР-000002;Окно, касса\n"
    rows, _ = exchange.read_accounting(ledger)
    assert [r.inventory_number for r in rows] == ["ЗР-000001", "ЗР-000002"]


def test_plan_id_column_matches_objects_without_numbers(plan: Plan) -> None:
    rows, _ = exchange.read_accounting("Идентификатор на плане;Наименование\neq_win_1;Окно\n")
    result = exchange.reconcile(plan, rows)
    assert [(m.object_id, m.matched_by) for m in result.matched] == [("eq_win_1", "идентификатор на плане")]


def test_duplicate_numbers_on_the_plan_are_matched_in_turn(plan: Plan) -> None:
    doc = plan.model_dump(by_alias=True, exclude_none=True)
    doc["equipment"][1]["inventory"]["inventory_number"] = doc["equipment"][0]["inventory"]["inventory_number"]
    twin = Plan.model_validate(doc)
    number = doc["equipment"][0]["inventory"]["inventory_number"]
    rows, _ = exchange.read_accounting(f"Инвентарный номер\n{number}\n{number}\n")
    result = exchange.reconcile(twin, rows)
    assert len(result.matched) == 2 and not result.missing_on_plan
