"""Обучение ускорителя поиска места.

Данные сервис порождает сам: для каждой позиции, прошедшей быстрый фильтр, запускается полная
проверка, и её исход становится меткой. На этих парах «признаки позиции → прошла или нет»
обучается градиентный бустинг на решающих деревьях.

Бустинг написан в scripts/gbdt.py на numpy: ни обучение, ни сервис не тянут внешних библиотек
машинного обучения. В сервис уезжают только деревья в JSON, вывод по ним — в
app/assistant/accelerator.py. Экспорт сверяется с обученной моделью: расхождение обязано быть
машинным нулём.

Запуск из services/api:
    uv run python scripts/train_accelerator.py --plans ../../data/example_plan.json --synthetic 40

Реальные планы БТИ правилами хакатона запрещены, поэтому разнообразие даёт генератор
синтетических отделений.
"""

from __future__ import annotations

import argparse
import json
import multiprocessing as mp
import random
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

from app.assistant import accelerator, search

import gbdt
from app.dictionaries import equipment_types
from app.models import Equipment, Plan

TRAIN_TYPES = (
    "cashier_window",
    "pvz_counter",
    "storage_rack",
    "waiting_seat",
    "self_service_kiosk",
    "atm",
    "info_stand",
    "package_drop_box",
)
SEED = 20260918
SIZES = {
    "cashier_window": (1.2, 0.8),
    "storage_rack": (2.0, 0.6),
    "waiting_seat": (2.0, 0.5),
    "atm": (0.7, 0.5),
    "pvz_counter": (2.0, 0.8),
}


def synthetic_plan(index: int, rng: random.Random) -> Plan:
    """Случайное отделение: размеры, служебная зона, зона ожидания и уже стоящее оборудование."""
    width = rng.uniform(7.0, 20.0)
    depth = rng.uniform(5.5, 14.0)
    service = rng.uniform(2.5, max(2.6, min(6.0, width - 4.0)))
    cut = width - service
    wait_depth = min(3.5, depth - 2.0)
    document: dict = {
        "ops": {"id": f"train_{index}", "name": f"Учебное отделение {index + 1}"},
        "walls": [
            {"id": "w_s", "from": [0, 0], "to": [width, 0], "type": "outer"},
            {"id": "w_e", "from": [width, 0], "to": [width, depth], "type": "outer"},
            {"id": "w_n", "from": [width, depth], "to": [0, depth], "type": "outer"},
            {"id": "w_w", "from": [0, depth], "to": [0, 0], "type": "outer"},
            {"id": "w_p", "from": [cut, 0], "to": [cut, depth], "type": "partition"},
        ],
        "doors": [
            {
                "id": "d_main",
                "wall": [rng.uniform(0.5, max(0.6, cut - 2.0)), depth, 0, depth],
                "type": "entrance",
                "width_m": 1.5,
            },
            {
                "id": "d_int",
                "wall": [cut, depth / 2 - 0.45, cut, depth / 2 + 0.45],
                "type": "internal",
                "width_m": 0.9,
            },
        ],
        "zones": [
            {
                "id": "z_client",
                "type": "client_area",
                "label": "Клиентская зона",
                "polygon": [[0, 0], [cut, 0], [cut, depth], [0, depth]],
            },
            {
                "id": "z_service",
                "type": "back_office",
                "label": "Служебная зона",
                "polygon": [[cut, 0], [width, 0], [width, depth], [cut, depth]],
            },
            {
                "id": "z_wait",
                "type": "waiting",
                "label": "Зона ожидания",
                "polygon": [
                    [0.2, depth - wait_depth],
                    [cut - 0.2, depth - wait_depth],
                    [cut - 0.2, depth - 0.2],
                    [0.2, depth - 0.2],
                ],
            },
        ],
        "equipment": [],
    }
    main = document["doors"][0]["wall"]
    main[2] = main[0] + 1.5
    for slot in range(rng.randint(0, 6)):
        type_code = rng.choice(list(SIZES))
        size = SIZES[type_code]
        document["equipment"].append(
            {
                "id": f"eq_{slot}",
                "type": type_code,
                "position": [round(rng.uniform(1.0, max(1.1, cut - 1.0)), 2), round(rng.uniform(1.0, depth - 1.0), 2)],
                "size_m": list(size),
                "rotation_deg": rng.choice([0.0, 90.0]),
            }
        )
    return Plan.model_validate(document)


def collect_one(task: tuple[dict, int, int]) -> tuple[list[list[float]], list[int], list[str]]:
    """Размеченные позиции для одного плана. Отдельная функция — чтобы считать в пуле процессов."""
    document, per_type, seed = task
    plan = Plan.model_validate(document)
    rng = random.Random(seed)
    known = equipment_types(plan)
    rows: list[list[float]] = []
    labels: list[int] = []
    groups: list[str] = []
    baseline = search.validate(plan, explain=False).errors
    for type_code in TRAIN_TYPES:
        if type_code not in known:
            continue
        candidates, _ = search.generate(plan, type_code)
        if not candidates:
            continue
        chosen = candidates if len(candidates) <= per_type else rng.sample(candidates, per_type)
        for candidate in chosen:
            fields = {
                "id": "__train__",
                "type": type_code,
                "position": candidate.position,
                "rotation_deg": candidate.rotation_deg,
                "size_m": known[type_code].default_size_m,
            }
            if candidate.operator_position is not None:
                fields["operator_position"] = candidate.operator_position
            item = Equipment.model_validate(fields)
            evaluated = search.evaluate(plan, search.with_equipment(plan, item), candidate, baseline)
            rows.append([float(candidate.features.get(name, 0.0)) for name in accelerator.FEATURES])
            labels.append(1 if evaluated.accepted else 0)
            groups.append(f"{plan.ops.id}:{type_code}")
    return rows, labels, groups


def collect(plans: list[Plan], per_type: int, workers: int) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    tasks = [
        (plan.model_dump(by_alias=True, exclude_none=True), per_type, SEED + index)
        for index, plan in enumerate(plans)
    ]
    rows: list[list[float]] = []
    labels: list[int] = []
    groups: list[str] = []
    with mp.Pool(processes=workers) as pool:
        for index, (part_rows, part_labels, part_groups) in enumerate(pool.imap_unordered(collect_one, tasks), 1):
            rows.extend(part_rows)
            labels.extend(part_labels)
            groups.extend(part_groups)
            print(f"  план {index}/{len(tasks)}: всего примеров {len(labels)}", flush=True)
    return np.array(rows, dtype=float), np.array(labels, dtype=float), np.array(groups)


def precision_at_k(order: np.ndarray, labels: np.ndarray, groups: np.ndarray, k: int = 10) -> float:
    """Доля подходящих мест в первых k позициях списка — то, ради чего модель и нужна."""
    hits, total = 0, 0
    for group in np.unique(groups):
        mask = groups == group
        best = np.argsort(-order[mask])[:k]
        hits += int(labels[mask][best].sum())
        total += len(best)
    return hits / total if total else 0.0


def export(model: gbdt.Booster, metrics: dict, samples: int) -> dict:
    trees = [
        {
            "feature": tree.feature,
            # Порог округлять нельзя: он взят из самих данных, и строка с таким же значением
            # после округления вниз ушла бы в другую ветку.
            "threshold": [float(t) for t in tree.threshold],
            "left": tree.left,
            "right": tree.right,
            "value": [round(float(v), 9) for v in tree.value],
        }
        for tree in model.trees
    ]
    importance = dict(zip(accelerator.FEATURES, (float(v) for v in model.importance)))
    return {
        "kind": "gradient_boosting",
        "features": list(accelerator.FEATURES),
        "trees": trees,
        "init": round(model.init, 6),
        "learning_rate": model.learning_rate,
        "importance": {k: round(v, 5) for k, v in sorted(importance.items(), key=lambda kv: -kv[1])},
        "metrics": metrics,
        "trained_at": datetime.now(timezone.utc).date().isoformat(),
        "samples": samples,
    }


def roc_auc(y: np.ndarray, probability: np.ndarray) -> float:
    """Площадь под ROC через ранги: доля правильно упорядоченных пар «подходит — не подходит»."""
    positives, negatives = probability[y >= 0.5], probability[y < 0.5]
    if len(positives) == 0 or len(negatives) == 0:
        return 0.5
    order = np.argsort(np.concatenate([positives, negatives]))
    ranks = np.empty(len(order), dtype=float)
    ranks[order] = np.arange(1, len(order) + 1)
    return float((ranks[: len(positives)].sum() - len(positives) * (len(positives) + 1) / 2) / (len(positives) * len(negatives)))


def main() -> None:
    parser = argparse.ArgumentParser(description="Обучение ускорителя поиска места")
    parser.add_argument("--plans", nargs="+", required=True)
    parser.add_argument("--out", default="app/assistant/accelerator_model.json")
    parser.add_argument("--per-type", type=int, default=160)
    parser.add_argument("--synthetic", type=int, default=40)
    parser.add_argument("--trees", type=int, default=400)
    parser.add_argument("--depth", type=int, default=5)
    parser.add_argument("--rate", type=float, default=0.08)
    parser.add_argument("--workers", type=int, default=max(1, mp.cpu_count() - 2))
    parser.add_argument("--cache", default="", help="Файл выборки: собрать один раз, обучать много")
    args = parser.parse_args()

    rng = random.Random(SEED)
    plans = [Plan.model_validate(json.loads(Path(p).read_text(encoding="utf-8"))) for p in args.plans]
    plans.extend(synthetic_plan(index, rng) for index in range(args.synthetic))
    print(f"Планов для обучения: {len(plans)}, процессов: {args.workers}", flush=True)

    cache = Path(args.cache) if args.cache else None
    if cache is not None and cache.exists():
        stored = np.load(cache, allow_pickle=True)
        x, y, groups = stored["x"], stored["y"], stored["groups"]
        print(f"Выборка взята из {cache}: {len(y)} примеров", flush=True)
    else:
        x, y, groups = collect(plans, args.per_type, args.workers)
        if cache is not None:
            np.savez_compressed(cache, x=x, y=y, groups=groups)
    if len(y) < 1000:
        raise SystemExit(f"Слишком мало примеров: {len(y)}")

    # Деление по планам, а не по строкам: модель проверяется на помещениях, которых не видела.
    unique_groups = sorted({g.split(":")[0] for g in groups})
    holdout = set(unique_groups[: max(1, len(unique_groups) // 5)])
    is_test = np.array([g.split(":")[0] in holdout for g in groups])
    x_train, y_train = x[~is_test], y[~is_test]
    x_test, y_test, groups_test = x[is_test], y[is_test], groups[is_test]

    model = gbdt.train(
        x_train,
        y_train,
        trees=args.trees,
        learning_rate=args.rate,
        max_depth=args.depth,
        seed=SEED,
        validation=(x_test, y_test),
    )

    probability = model.probability(x_test)
    accuracy = float(((probability >= 0.5) == (y_test >= 0.5)).mean())
    auc = roc_auc(y_test, probability)
    baseline_order = -x_test[:, list(accelerator.FEATURES).index("fitness")]
    metrics = {
        "доля верных ответов": round(accuracy, 4),
        "площадь под ROC": round(auc, 4),
        "подходящих в первых 10 — модель": round(precision_at_k(probability, y_test, groups_test), 4),
        "подходящих в первых 10 — прежний порядок": round(precision_at_k(baseline_order, y_test, groups_test), 4),
        "доля подходящих мест в выборке": round(float(y.mean()), 4),
    }

    exported = export(model, metrics, int(len(y)))
    Path(args.out).write_text(json.dumps(exported, ensure_ascii=False, indent=1), encoding="utf-8")

    # Сверка: наш вывод по JSON обязан совпадать с библиотечным до последнего знака.
    accelerator.reset()
    ours = accelerator.load(Path(args.out))
    assert ours is not None
    sample = x_test[:200]
    theirs = model.raw(sample)
    mine = np.array(
        [ours.init + ours.learning_rate * sum(tree.predict(list(row)) for tree in ours.trees) for row in sample]
    )
    difference = float(np.abs(theirs - mine).max())
    print(f"Примеров: {len(y)}, деревьев: {len(model.trees)}, метрики: {metrics}")
    print(f"Расхождение экспорта: {difference:.2e}")
    print("Влияние признаков:", list(exported["importance"].items())[:8])
    if difference > 1e-6:
        raise SystemExit("Экспорт деревьев расходится с обученной моделью")


if __name__ == "__main__":
    main()
