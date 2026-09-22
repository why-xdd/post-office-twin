"""Очередь по фактическим данным: журнал электронной очереди и счётчик посетителей."""

from __future__ import annotations

import importlib
import json
from pathlib import Path
from typing import Iterator

import pytest
from fastapi.testclient import TestClient

from app.assistant import queue_log
from app.models import Plan

DATA = Path(__file__).resolve().parents[3] / "data"
DEMO = DATA / "demo_plan.json"
LOG = DATA / "queue_log_sample.csv"


@pytest.fixture(scope="module")
def plan() -> Plan:
    return Plan.model_validate(json.loads(DEMO.read_text(encoding="utf-8")))


@pytest.fixture(scope="module")
def report(plan: Plan) -> queue_log.QueueLogReport:
    return queue_log.analyze(plan, *queue_log.read(LOG.read_text(encoding="utf-8")))


def test_ticket_log_is_recognised(report: queue_log.QueueLogReport) -> None:
    assert report.source == "журнал электронной очереди"
    assert report.days == 5 and report.skipped_rows == 0
    assert {g.name for g in report.groups} == {"Окна обслуживания", "Выдача посылок"}


def test_peaks_found_in_data_match_the_plan(report: queue_log.QueueLogReport) -> None:
    """Журнал смоделирован по часам пик из плана — анализ обязан найти их же."""
    assert report.peak_hours == report.declared_peak_hours == ["12:00-14:00", "17:00-19:00"]


def test_model_agrees_with_measured_wait(report: queue_log.QueueLogReport) -> None:
    """Модель M/M/c и журнал расходятся не больше чем на 4,5 минуты.

    Больше всего — в час сразу после пика: модель считает установившийся режим и не видит хвост
    очереди, оставшийся от предыдущего часа.
    """
    for group in report.groups:
        for hour in group.hours:
            if hour.model_wait_min is not None and hour.measured_wait_min is not None:
                assert abs(hour.model_wait_min - hour.measured_wait_min) < 4.5


def test_parcel_desk_is_short_in_the_evening_peak(report: queue_log.QueueLogReport) -> None:
    parcels = next(g for g in report.groups if g.name == "Выдача посылок")
    assert parcels.windows_on_plan == 1
    assert any(h.short and h.windows_needed == 2 for h in parcels.hours)
    assert any("Выдача посылок" in line and "2 окна" in line for line in report.summary)


def test_hourly_counter_uses_route_shares(plan: Plan) -> None:
    text = "Дата;Час;Посетителей\n15.09.2026;09:00;40\n15.09.2026;12:00;90\n"
    source, records, skipped = queue_log.read(text)
    result = queue_log.analyze(plan, source, records, skipped)
    assert source == "счётчик посетителей"
    windows = next(g for g in result.groups if g.name == "Окна обслуживания")
    assert windows.hours[0].arrivals_per_hour == pytest.approx(40 * 0.55, abs=0.1)
    assert windows.hours[0].measured_wait_min is None


def test_unknown_file_is_explained() -> None:
    with pytest.raises(ValueError, match="Выдан"):
        queue_log.read("a;b\n1;2\n")


@pytest.fixture()
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    monkeypatch.setenv("OPS_TWIN_DB", str(tmp_path / "plans.sqlite"))
    import app.main as main

    importlib.reload(main)
    with TestClient(main.app) as test_client:
        yield test_client


def test_api(client: TestClient) -> None:
    response = client.post(
        "/api/capacity/log",
        data={"document": DEMO.read_text(encoding="utf-8")},
        files={"file": ("журнал.csv", LOG.read_bytes(), "text/csv")},
    )
    assert response.status_code == 200, response.text
    assert response.json()["days"] == 5


def test_time_only_log_crosses_midnight_and_is_one_day(plan: Plan) -> None:
    text = "Выдан;Вызван;Завершён\n23:58:00;00:03:00;00:05:00\n10:00:00;10:01:00;10:04:00\n"
    source, records, skipped = queue_log.read(text)
    assert skipped == 0 and records[0]["wait"] == pytest.approx(5.0)
    result = queue_log.analyze(plan, source, records, skipped)
    assert result.days == 1
    assert any("нет дат" in line for line in result.assumptions)


def test_counter_reads_thousands_with_non_breaking_space(plan: Plan) -> None:
    _, records, skipped = queue_log.read("Час;Посетителей\n12:00;1 234\n")
    assert skipped == 0 and records[0]["count"] == 1234


def test_peak_never_runs_past_midnight() -> None:
    assert queue_log._peaks({23: 10.0}) == ["23:00-24:00"]
