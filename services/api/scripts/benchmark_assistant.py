"""Замер качества ассистента: что даёт обученный ускоритель.

Считает один и тот же сценарий трижды — без модели, с одной головой и с обеими — и печатает
таблицу: время, оставшиеся нарушения, общую оценку расстановки и её слагаемые. Числа из этой
таблицы идут в README и в презентацию, поэтому замер должен воспроизводиться одной командой:

    uv run python scripts/benchmark_assistant.py --plans ../../data/example_plan.json
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

from app.assistant import accelerator, objective, recommend, search
from app.geometry import passability
from app.models import Plan


def measure(plan: Plan, with_model: bool, with_quality: bool = True) -> dict:
    """Один и тот же сценарий при разной оснастке поиска."""
    accelerator.reset()
    # Замер должен считать заново: кэш геометрии живёт между вызовами и исказил бы время.
    passability.reset_caches()
    original = accelerator.MODEL_PATH
    weight = search.QUALITY_WEIGHT
    if not with_model:
        accelerator.MODEL_PATH = original.with_name("__нет_модели__.json")
    if not with_quality:
        # Вторая голова отключается весом: так видно, что даёт именно она.
        search.QUALITY_WEIGHT = 0.0
    try:
        started = time.perf_counter()
        report = recommend.improve(plan, fixes_only=True)
        elapsed = time.perf_counter() - started
        improved = Plan.model_validate(report.improved_plan)
        score = objective.score(improved, plan)
        return {
            "секунд": round(elapsed, 1),
            "шагов": len(report.steps),
            "ошибок": score.errors,
            "предупреждений": score.warnings,
            "оценка": score.total,
            "проход у входа, м": score.entrance_width_m,
            "путь до окон, м": score.walk_m,
            "мест ожидания": score.waiting_capacity,
        }
    finally:
        accelerator.MODEL_PATH = original
        search.QUALITY_WEIGHT = weight
        accelerator.reset()


def main() -> None:
    parser = argparse.ArgumentParser(description="Замер качества ассистента")
    parser.add_argument("--plans", nargs="+", required=True)
    args = parser.parse_args()

    model = accelerator.load()
    print("Модель:", "нет" if model is None else f"{model.samples} примеров, {model.metrics}")
    for path in args.plans:
        plan = Plan.model_validate(json.loads(Path(path).read_text(encoding="utf-8")))
        before = objective.score(plan)
        print(f"\n{path}: было ошибок {before.errors}, оценка {before.total}")
        runs = (
            ("без модели", False, False),
            ("одна голова", True, False),
            ("две головы", True, True),
        )
        for label, with_model, with_quality in runs:
            result = measure(plan, with_model, with_quality)
            print(f"  {label:12}: " + ", ".join(f"{k} {v}" for k, v in result.items()))


if __name__ == "__main__":
    main()
