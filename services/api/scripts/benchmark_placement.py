"""Замер подбора места для нового объекта: верхушка списка против полного перебора.

Сервис проверяет полностью только первые места списка. Здесь для каждого плана и типа объекта
считается, что выбрал бы сервис при разной оснастке поиска, и сравнивается с лучшим местом,
найденным полным перебором всех кандидатов. Планы — свежие синтетические (другое зерно, чем при
обучении, модель их не видела) и два настоящих из data/.

Запуск из services/api:
    uv run python scripts/benchmark_placement.py --synthetic 24
"""

from __future__ import annotations

import argparse
import json
import multiprocessing as mp
import random
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))

from train_accelerator import TRAIN_TYPES, synthetic_plan  # noqa: E402

from app.assistant import accelerator, search  # noqa: E402
from app.assistant.search import equipment_types  # noqa: E402
from app.models import Plan  # noqa: E402

ROOT = Path(__file__).resolve().parents[3]
SEED = 4242
TOP_K = (12, search.DEFAULT_TOP_K)
EVERYTHING = 10**6
# Название, есть ли модель, вес второй головы.
SETUPS = (
    ("без модели", False, 0.0),
    ("одна голова", True, 0.0),
    ("две головы", True, search.QUALITY_WEIGHT),
)


def _best(plan: Plan, type_code: str, top_k: int) -> tuple[float | None, float]:
    result = search.place(plan, type_code, new_id="__new__", top_k=top_k, keep=1)
    return (result.best[0].score.total if result.best else None), result.elapsed_ms / 1000


def one_task(task: tuple[dict, str]) -> dict:
    document, type_code = task
    plan = Plan.model_validate(document)
    original, weight = accelerator.MODEL_PATH, search.QUALITY_WEIGHT
    missing = original.with_name("__нет_модели__.json")
    out: dict = {}
    try:
        accelerator.MODEL_PATH = missing
        accelerator.reset()
        out["перебор"] = _best(plan, type_code, EVERYTHING)
        for name, with_model, quality_weight in SETUPS:
            accelerator.MODEL_PATH = original if with_model else missing
            accelerator.reset()
            search.QUALITY_WEIGHT = quality_weight
            for top_k in TOP_K:
                out[f"{name}|{top_k}"] = _best(plan, type_code, top_k)
    finally:
        accelerator.MODEL_PATH, search.QUALITY_WEIGHT = original, weight
        accelerator.reset()
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description="Замер подбора места для нового объекта")
    parser.add_argument("--synthetic", type=int, default=24)
    parser.add_argument("--workers", type=int, default=max(1, mp.cpu_count() - 2))
    args = parser.parse_args()

    rng = random.Random(SEED)
    plans = [synthetic_plan(5000 + index, rng) for index in range(args.synthetic)]
    for name in ("example_plan", "demo_plan"):
        plans.append(Plan.model_validate(json.loads((ROOT / "data" / f"{name}.json").read_text(encoding="utf-8"))))
    tasks = []
    for plan in plans:
        known = equipment_types(plan)
        document = plan.model_dump(by_alias=True, exclude_none=True)
        tasks += [(document, type_code) for type_code in TRAIN_TYPES if type_code in known]

    with mp.Pool(args.workers) as pool:
        results = [r for r in pool.map(one_task, tasks, chunksize=1) if r["перебор"][0] is not None]

    print(f"Планов: {len(plans)}, задач «поставить объект»: {len(results)}")
    print(f"Полный перебор: {np.mean([r['перебор'][1] for r in results]):.2f} с на задачу")
    print("| Порядок проверки | Проверяется мест | Проигрыш перебору | Нашёл лучшее | Не нашёл места | Секунд |")
    print("|---|---|---|---|---|---|")
    for name, _, _ in SETUPS:
        for top_k in TOP_K:
            losses, hits, misses, seconds = [], 0, 0, []
            for r in results:
                best = r["перебор"][0]
                got, elapsed = r[f"{name}|{top_k}"]
                seconds.append(elapsed)
                if got is None:
                    misses += 1
                    continue
                losses.append(got - best)
                hits += int(got <= best + 1e-9)
            print(
                f"| {name} | {top_k} | {np.mean(losses):.2f} | {hits / len(results):.1%} | {misses} "
                f"| {np.mean(seconds):.2f} |"
            )


if __name__ == "__main__":
    main()
