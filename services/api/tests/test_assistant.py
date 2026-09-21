"""Проверки ассистента: объяснимость, эффект и отсутствие ухудшений."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.assistant import capacity, recommend
from app.geometry import passability
from app.models import Plan
from app.rules.engine import VISITOR_TARGET_TYPES, validate

SAMPLE = Path(__file__).resolve().parents[3] / "data" / "example_plan.json"


@pytest.fixture(scope="module")
def plan() -> Plan:
    return Plan.model_validate(json.loads(SAMPLE.read_text(encoding="utf-8")))


@pytest.fixture(scope="module")
def improvement(plan: Plan) -> recommend.ImprovementReport:
    return recommend.improve(plan)


def test_bottleneck_culprits_are_the_objects_at_the_entrance(plan: Plan) -> None:
    """Сужение у входа создают банкомат и скамья, а не окна, к которым ведёт путь."""
    blockers = passability.bottleneck_blockers(plan, VISITOR_TARGET_TYPES, 1.2)
    culprits = {c for group in blockers.values() for c in group}
    assert {"eq_atm", "eq_bench_1"} <= culprits


def test_path_fix_widens_entrance_to_norm(plan: Plan) -> None:
    rec = recommend.recommend_path_fix(plan, recommend.summarize(plan))
    assert rec is not None
    after = recommend.apply_changes(plan, rec.changes)
    assert (passability.widest_route_width(after, VISITOR_TARGET_TYPES) or 0) >= 1.2
    assert {c.object_id for c in rec.changes} >= {"eq_atm", "eq_bench_1"}


def test_path_fix_effects_match_its_summary(plan: Plan) -> None:
    rec = recommend.recommend_path_fix(plan, recommend.summarize(plan))
    assert rec is not None
    effects = {e.key: e for e in rec.effects}
    entrance = effects["entrance_route_width"]
    assert entrance.after is not None and entrance.after >= 1.2
    assert f"{entrance.before} → {entrance.after} м" in rec.summary
    # Оставшееся местное сужение названо в тексте, а не спрятано за общей шириной прохода.
    narrowest = effects["min_route_width"]
    if narrowest.after is not None and narrowest.after < 1.2:
        assert f"{narrowest.after} м" in rec.summary


def test_every_recommendation_explains_itself(improvement: recommend.ImprovementReport) -> None:
    assert improvement.steps
    for step in improvement.steps:
        rec = step.recommendation
        assert rec.reasons, rec.id
        assert rec.changes, rec.id
        assert rec.effects, rec.id
        assert all(change.object_id for change in rec.changes)


def test_fix_recommendations_cite_a_norm(improvement: recommend.ImprovementReport) -> None:
    fixes = [s.recommendation for s in improvement.steps if s.recommendation.scenario == "fix"]
    assert fixes
    assert all(any(r.source for r in rec.reasons) for rec in fixes)


def test_improvement_never_adds_errors(improvement: recommend.ImprovementReport) -> None:
    previous = improvement.initial["errors"]
    for step in improvement.steps:
        assert step.errors_after <= previous
        previous = step.errors_after


def test_improved_plan_has_no_errors(improvement: recommend.ImprovementReport) -> None:
    improved = Plan.model_validate(improvement.improved_plan)
    assert validate(improved).errors == 0
    assert improvement.final["min_route_width"] >= 1.2


def test_changes_reproduce_the_improved_plan(plan: Plan, improvement: recommend.ImprovementReport) -> None:
    """Рекомендации применимы в редакторе: последовательное применение даёт тот же план."""
    current = plan
    for step in improvement.steps:
        current = recommend.apply_changes(current, step.recommendation.changes)
    assert validate(current).errors == validate(Plan.model_validate(improvement.improved_plan)).errors


def test_erlang_c_known_value() -> None:
    """Эталон: 2 канала, нагрузка 1 Эрланг — вероятность ожидания 1/3."""
    assert capacity.erlang_c(2, 1.0) == pytest.approx(1 / 3, abs=1e-6)


def test_more_windows_never_increase_wait() -> None:
    assert capacity.windows_needed(60, 3.0) >= capacity.windows_needed(20, 3.0)


def test_place_equipment_does_not_add_errors(plan: Plan) -> None:
    rec = recommend.place_equipment(plan, "storage_rack")
    before = validate(plan).errors
    if rec.changes:
        after = validate(recommend.apply_changes(plan, rec.changes)).errors
        assert after <= before


def test_fixes_only_removes_errors_without_adding_equipment(plan: Plan) -> None:
    report = recommend.improve(plan, fixes_only=True)
    assert report.final["errors"] == 0
    assert all(step.recommendation.scenario == "fix" for step in report.steps)
    assert report.final["equipment_total"] == report.initial["equipment_total"]


def test_fixes_never_block_doors(plan: Plan) -> None:
    from app.geometry import core

    report = recommend.improve(plan, fixes_only=True)
    assert core.blocked_doors(Plan.model_validate(report.improved_plan)) == []


def test_search_leaves_no_slivers_between_object_and_wall(plan: Plan) -> None:
    """Место либо вплотную к стене, либо с зазором, в который можно пройти или поставить объект."""
    from app.assistant import search
    from app.geometry import core

    found = search.place(plan, "storage_rack", new_id="rack_new", top_k=24, keep=5)
    shape = core.outline(plan)
    assert found.best
    for option in found.best:
        gap = shape.exterior.distance(option.candidate.polygon)
        assert gap < 1e-6 or gap >= search.SETTLE_WALL_M - 1e-6, gap
        assert shape.contains(option.candidate.polygon.buffer(-1e-6))


def test_objective_prefers_wider_entrance_and_seats(plan: Plan) -> None:
    """Оценка расстановки должна ставить доступность и места ожидания выше мелких удобств."""
    from app.assistant import objective

    base = objective.score(plan)
    narrow = plan.model_copy(deep=True)
    # Скамья возвращается вплотную к входу: проход снова сужается.
    bench = next(e for e in narrow.equipment if e.id == "eq_bench_1")
    bench.position = (1.5, 6.5)
    assert objective.score(narrow).total >= base.total

    seats_gone = plan.model_copy(deep=True)
    seats_gone.equipment = [e for e in seats_gone.equipment if e.id != "eq_bench_1"]
    assert objective.score(seats_gone).waiting_capacity < base.waiting_capacity


def test_improvement_does_not_make_the_plan_worse_overall(plan: Plan) -> None:
    """Устранение нарушений не должно ухудшать план по остальным показателям."""
    from app.assistant import objective

    report = recommend.improve(plan, fixes_only=True)
    improved = Plan.model_validate(report.improved_plan)
    before = objective.score(plan)
    after = objective.score(improved, plan)
    assert after.total < before.total
    assert after.errors == 0
    # Путь посетителя до окон не должен вырасти ради снятия ошибок.
    assert after.walk_m is not None and before.walk_m is not None
    assert after.walk_m <= before.walk_m + 0.5


def test_command_in_plain_russian_becomes_recommendations(plan: Plan) -> None:
    """Фраза словами превращается в те же рекомендации, что и панель ассистента."""
    result = recommend.run_command(plan, "поставь две кассы у перегородки")
    assert result.source == "разбор фразы"
    assert result.understood and "Окно" in result.understood[0]
    assert len(result.recommendations) == 2
    assert all(rec.changes for rec in result.recommendations)
    # Места разные: второй объект не встаёт на место первого.
    positions = {tuple(rec.changes[0].position or ()) for rec in result.recommendations}
    assert len(positions) == 2


def test_command_reports_what_it_did_not_understand(plan: Plan) -> None:
    result = recommend.run_command(plan, "сделай красиво и современно")
    assert not result.recommendations
    assert result.unknown


def test_remove_command_shows_the_effect(plan: Plan) -> None:
    result = recommend.run_command(plan, "убери банкомат")
    assert len(result.recommendations) == 1
    recommendation = result.recommendations[0]
    assert recommendation.changes[0].op == "remove"
    errors = next(e for e in recommendation.effects if e.key == "errors")
    assert errors.after is not None and errors.before is not None and errors.after < errors.before


def test_conclusion_is_built_from_computed_numbers(plan: Plan) -> None:
    """Заключение собирается по посчитанным числам: каждое число в тексте есть в фактах."""
    from app.assistant import conclusion

    result = conclusion.build(plan)
    assert "ОПС-1" in result.text
    assert "нарушени" in result.text
    # Внутренние идентификаторы в текст для заказчика не попадают.
    assert "eq_" not in result.text
    assert result.facts["нарушений"] == 7
    assert result.facts["после_исправления"]["нарушений"] == 0
