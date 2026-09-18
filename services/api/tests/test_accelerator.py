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
    accelerator.reset()
    model = accelerator.load()
    assert model is not None, "файл весов должен поставляться вместе с сервисом"
    assert model.samples >= 100
    assert model.metrics["площадь под ROC"] > 0.6
    assert len(model.explain()) == len(accelerator.FEATURES)


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
