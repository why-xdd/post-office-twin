"""Очередь в пик на плане: сценарий нагрузки → минуты → люди на чертеже → проход."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.assistant import peak
from app.models import Plan

DEMO = Path(__file__).resolve().parents[3] / "data" / "demo_plan.json"


@pytest.fixture(scope="module")
def plan() -> Plan:
    return Plan.model_validate(json.loads(DEMO.read_text(encoding="utf-8")))


def test_usual_peak_fits_the_accumulator(plan: Plan) -> None:
    report = peak.analyze(plan)
    assert report.available
    assert all(group.meets_target for group in report.groups)
    assert report.outside == 0
    assert report.passage_with_queue_m == report.passage_without_queue_m


def test_seasonal_peak_spills_out_and_narrows_the_passage(plan: Plan) -> None:
    report = peak.analyze(plan, peak.PeakScenario(flow_factor=1.4))
    windows = next(g for g in report.groups if g.name == "Окна обслуживания")
    assert not windows.meets_target
    assert report.outside > 0
    assert report.passage_with_queue_m is not None and report.passage_without_queue_m is not None
    assert report.passage_with_queue_m < report.passage_without_queue_m
    # Каждый человек очереди стоит на свободном полу, а не в оборудовании.
    from shapely.geometry import Point

    from app.geometry import passability

    space = passability.free_space(plan)
    assert all(space.contains(Point(p.x, p.y)) for p in report.people)


def test_broken_window_makes_it_worse_and_repair_is_advised(plan: Plan) -> None:
    usual = peak.analyze(plan, peak.PeakScenario(flow_factor=1.4))
    broken = peak.analyze(plan, peak.PeakScenario(flow_factor=1.4, out_of_service=["eq_win_2"]))
    count = lambda r: next(g for g in r.groups if g.name == "Окна обслуживания").people  # noqa: E731
    assert count(broken) >= count(usual)
    assert any("после ремонта" in line for line in broken.advice)


def test_closed_window_advice_names_the_window_to_open(plan: Plan) -> None:
    report = peak.analyze(plan, peak.PeakScenario(closed=["eq_win_3"], flow_factor=1.2))
    windows = next(g for g in report.groups if g.name == "Окна обслуживания")
    if not windows.meets_target:
        assert any("откройте Окно №3" in line for line in report.advice)


def test_self_service_takes_load_off_the_windows(plan: Plan) -> None:
    before = peak.analyze(plan, peak.PeakScenario(flow_factor=1.4))
    after = peak.analyze(plan, peak.PeakScenario(flow_factor=1.4, self_service_share=0.5))
    wait = lambda r: next(g for g in r.groups if g.name == "Окна обслуживания").mean_wait_min  # noqa: E731
    assert wait(after) is not None and (wait(before) is None or wait(after) < wait(before))
    assert any(g.name == "Самообслуживание" for g in after.groups)


def test_plan_without_flow_is_reported_not_crashed(plan: Plan) -> None:
    empty = plan.model_copy(update={"flow_hints": None})
    report = peak.analyze(empty)
    assert not report.available and report.reason
