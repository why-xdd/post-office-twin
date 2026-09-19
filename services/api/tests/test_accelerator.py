"""Ускоритель поиска: загрузка модели, устойчивость к её отсутствию, влияние на порядок."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.assistant import accelerator, search
from app.models import Plan

SAMPLE = Path(__file__).resolve().parents[3] / "data" / "example_plan.json"


@pytest.fixture(scope="module")
def plan() -> Plan:
    return Plan.model_validate(json.loads(SAMPLE.read_text(encoding="utf-8")))


def test_model_is_shipped_and_reports_its_quality() -> None:
    """Модель едет вместе с сервисом и честно сообщает, на чём и с каким качеством обучена."""
    accelerator.reset()
    model = accelerator.load()
    assert model is not None, "файл модели должен поставляться вместе с сервисом"
    assert model.samples >= 1000
    assert model.trees
    assert model.metrics["площадь под ROC"] > 0.8
    # Ради этого модель и нужна: в первых десяти местах подходящих должно быть больше,
    # чем при прежнем порядке по написанной вручную оценке уместности.
    assert model.metrics["подходящих в первых 10 — модель"] >= model.metrics["подходящих в первых 10 — прежний порядок"]
    assert len(model.explain()) == len(accelerator.FEATURES)


def test_second_head_earns_its_place() -> None:
    """Вторая голова обязана улучшать выбор, а не просто присутствовать в файле."""
    accelerator.reset()
    model = accelerator.load()
    assert model is not None and model.quality is not None, "в поставке должны быть обе головы"
    metrics = model.metrics
    # Проигрыш лучшему месту: чем меньше, тем ближе выбор сервиса к идеальному.
    assert metrics["проигрыш лучшему месту — две головы"] < metrics["проигрыш лучшему месту — одна голова"]
    assert metrics["проигрыш лучшему месту — одна голова"] < metrics["проигрыш лучшему месту — прежний порядок"]
    assert metrics["лучшее место в первых 10 — две головы"] >= metrics["лучшее место в первых 10 — прежний порядок"]


def test_file_without_second_head_still_loads(tmp_path: Path) -> None:
    """Файл прежнего образца обязан читаться: вторая голова — дополнение, а не условие."""
    accelerator.reset()
    data = json.loads(accelerator.MODEL_PATH.read_text(encoding="utf-8"))
    data.pop("quality")
    older = tmp_path / "модель.json"
    older.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    model = accelerator.load(older)
    assert model is not None and model.quality is None
    assert model.expected_score({"fitness": 1.0}) == 0.0


def test_rank_pairs_gives_both_answers(plan: Plan) -> None:
    """Порядок проверки строится по двум числам: вероятность и ожидаемое качество."""
    accelerator.reset()
    candidates, _ = search.generate(plan, "waiting_seat")
    pairs = accelerator.rank_pairs([c.features for c in candidates[:50]])
    assert pairs is not None
    assert all(0.0 <= probability <= 1.0 for probability, _ in pairs)
    assert len({round(quality, 6) for _, quality in pairs}) > 1, "вторая голова обязана различать места"


def test_probability_is_a_probability(plan: Plan) -> None:
    candidates, _ = search.generate(plan, "storage_rack")
    probabilities = accelerator.rank([c.features for c in candidates[:50]])
    assert probabilities is not None
    assert all(0.0 <= p <= 1.0 for p in probabilities)
    assert len(set(round(p, 6) for p in probabilities)) > 1, "модель обязана различать позиции"


def test_search_works_without_the_model(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, plan: Plan) -> None:
    """Без файла весов поиск обязан работать: модель ускоряет, но не является условием работы."""
    monkeypatch.setattr(accelerator, "MODEL_PATH", tmp_path / "нет.json")
    accelerator.reset()
    try:
        assert accelerator.rank([{"fitness": 1.0}]) is None
        found = search.place(plan, "storage_rack", new_id="rack_new", top_k=6, keep=2)
        assert found.best
    finally:
        accelerator.reset()


def test_model_puts_suitable_places_first(plan: Plan) -> None:
    """Порядок с моделью должен чаще давать проходящие проверку места, чем без неё."""
    accelerator.reset()
    candidates, _ = search.generate(plan, "storage_rack")
    assert candidates
    with_model = search._prioritise(candidates)[:10]
    baseline = search.validate(plan, explain=False).errors
    types = search.equipment_types(plan)

    def accepted(subset: list[search.Candidate]) -> int:
        count = 0
        for candidate in subset:
            item = search.Equipment(
                id="__probe__",
                type="storage_rack",
                position=candidate.position,
                rotation_deg=candidate.rotation_deg,
                size_m=types["storage_rack"].default_size_m,
            )
            if search.evaluate(plan, search.with_equipment(plan, item), candidate, baseline).accepted:
                count += 1
        return count

    assert accepted(with_model) >= accepted(candidates[:10])
