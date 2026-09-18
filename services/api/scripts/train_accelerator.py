"""Обучение ускорителя поиска места.

Данные сервис порождает сам: для каждой позиции, прошедшей быстрый фильтр, запускается полная
проверка, и её исход становится меткой. Дальше на этих парах «признаки позиции → прошла или
нет» обучается логистическая регрессия.

Запуск из services/api:
    uv run python scripts/train_accelerator.py --plans ../../data/example_plan.json --out app/assistant/accelerator_model.json

Модель детерминирована: один и тот же набор планов даёт одни и те же веса.
"""

from __future__ import annotations

import argparse
import json
import random
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

from app.assistant import search
from app.dictionaries import equipment_types
from app.models import Plan

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
SAMPLES_PER_TYPE = 120
SEED = 20260918


def synthetic_plans(count: int, rng: random.Random) -> list[Plan]:
    """Случайные отделения: разные размеры, служебная зона и уже стоящее оборудование.

    Обучение на одном плане заказчика дало бы модель, знающую только его. Синтетические
    помещения добавляют разнообразия, а реальные планы БТИ правилами хакатона запрещены.
    """
    plans: list[Plan] = []
    for index in range(count):
        width = rng.uniform(8.0, 18.0)
        depth = rng.uniform(6.0, 12.0)
        service = rng.uniform(2.5, min(5.0, width - 4.0))
        cut = width - service
        document = {
            "ops": {"id": f"train_{index}", "name": f"Учебное отделение {index + 1}"},
            "walls": [
                {"id": "w_s", "from": [0, 0], "to": [width, 0], "type": "outer"},
                {"id": "w_e", "from": [width, 0], "to": [width, depth], "type": "outer"},
                {"id": "w_n", "from": [width, depth], "to": [0, depth], "type": "outer"},
                {"id": "w_w", "from": [0, depth], "to": [0, 0], "type": "outer"},
                {"id": "w_p", "from": [cut, 0], "to": [cut, depth], "type": "partition"},
            ],
            "doors": [
                {"id": "d_main", "wall": [1.0, depth, 2.5, depth], "type": "entrance", "width_m": 1.5},
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
                    "polygon": [[0.2, depth - 3.0], [cut - 0.2, depth - 3.0], [cut - 0.2, depth - 0.2], [0.2, depth - 0.2]],
                },
            ],
            "equipment": [],
        }
        for slot in range(rng.randint(1, 5)):
            type_code = rng.choice(["cashier_window", "storage_rack", "waiting_seat", "atm"])
            size = {"cashier_window": (1.2, 0.8), "storage_rack": (2.0, 0.6), "waiting_seat": (2.0, 0.5), "atm": (0.7, 0.5)}[type_code]
            document["equipment"].append(
                {
                    "id": f"eq_{slot}",
                    "type": type_code,
                    "position": [round(rng.uniform(1.0, cut - 1.0), 2), round(rng.uniform(1.0, depth - 1.0), 2)],
                    "size_m": list(size),
                    "rotation_deg": rng.choice([0.0, 90.0]),
                }
            )
        plans.append(Plan.model_validate(document))
    return plans


def collect(plans: list[Plan], per_type: int) -> tuple[np.ndarray, np.ndarray]:
    """Признаки позиций и метки: 1 — полная проверка не добавила ошибок."""
    rows: list[list[float]] = []
    labels: list[int] = []
    rng = random.Random(SEED)
    for plan in plans:
        known = equipment_types(plan)
        for type_code in TRAIN_TYPES:
            if type_code not in known:
                continue
            candidates, _ = search.generate(plan, type_code)
            if not candidates:
                continue
            chosen = candidates if len(candidates) <= per_type else rng.sample(candidates, per_type)
            baseline = search.validate(plan, explain=False).errors
            for candidate in chosen:
                item = search.Equipment(
                    id="__train__",
                    type=type_code,
                    position=candidate.position,
                    rotation_deg=candidate.rotation_deg,
                    size_m=known[type_code].default_size_m,
                    **({"operator_position": candidate.operator_position} if candidate.operator_position else {}),
                )
                evaluated = search.evaluate(
                    plan, search.with_equipment(plan, item), candidate, baseline
                )
                rows.append([float(candidate.features.get(name, 0.0)) for name in search.accelerator.FEATURES])
                labels.append(1 if evaluated.accepted else 0)
    return np.array(rows, dtype=float), np.array(labels, dtype=float)


def train(x: np.ndarray, y: np.ndarray, epochs: int = 600, rate: float = 0.35) -> dict:
    """Логистическая регрессия градиентным спуском на стандартизованных признаках."""
    mean = x.mean(axis=0)
    std = x.std(axis=0)
    std[std == 0] = 1.0
    normed = (x - mean) / std

    # Обучение и проверка делятся по порядку сбора, а не случайно: так проверка честнее.
    split = int(len(normed) * 0.8)
    x_train, y_train = normed[:split], y[:split]
    x_test, y_test = normed[split:], y[split:]

    weights = np.zeros(normed.shape[1])
    bias = 0.0
    for _ in range(epochs):
        z = x_train @ weights + bias
        prediction = 1.0 / (1.0 + np.exp(-np.clip(z, -30, 30)))
        error = prediction - y_train
        weights -= rate * (x_train.T @ error) / len(x_train)
        bias -= rate * float(error.mean())

    def probabilities(matrix: np.ndarray) -> np.ndarray:
        return 1.0 / (1.0 + np.exp(-np.clip(matrix @ weights + bias, -30, 30)))

    test_probability = probabilities(x_test)
    accuracy = float(((test_probability >= 0.5) == (y_test >= 0.5)).mean()) if len(y_test) else 0.0
    return {
        "weights": dict(zip(search.accelerator.FEATURES, weights.round(5).tolist())),
        "bias": round(float(bias), 5),
        "mean": dict(zip(search.accelerator.FEATURES, mean.round(5).tolist())),
        "std": dict(zip(search.accelerator.FEATURES, std.round(5).tolist())),
        "metrics": {
            "доля верных ответов": round(accuracy, 4),
            "площадь под ROC": round(roc_auc(y_test, test_probability), 4),
            "доля подходящих мест в выборке": round(float(y.mean()), 4),
        },
        "trained_at": datetime.now(timezone.utc).date().isoformat(),
        "samples": int(len(y)),
    }


def roc_auc(y: np.ndarray, probability: np.ndarray) -> float:
    """Площадь под ROC-кривой через ранги: доля правильно упорядоченных пар."""
    positives = probability[y >= 0.5]
    negatives = probability[y < 0.5]
    if len(positives) == 0 or len(negatives) == 0:
        return 0.5
    order = np.argsort(np.concatenate([positives, negatives]))
    ranks = np.empty(len(order), dtype=float)
    ranks[order] = np.arange(1, len(order) + 1)
    rank_sum = ranks[: len(positives)].sum()
    return float((rank_sum - len(positives) * (len(positives) + 1) / 2) / (len(positives) * len(negatives)))


def main() -> None:
    parser = argparse.ArgumentParser(description="Обучение ускорителя поиска места")
    parser.add_argument("--plans", nargs="+", required=True, help="Файлы планов для обучения")
    parser.add_argument("--out", default="app/assistant/accelerator_model.json")
    parser.add_argument("--per-type", type=int, default=SAMPLES_PER_TYPE)
    parser.add_argument("--synthetic", type=int, default=8, help="Сколько учебных помещений добавить")
    args = parser.parse_args()

    plans = [Plan.model_validate(json.loads(Path(p).read_text(encoding="utf-8"))) for p in args.plans]
    plans.extend(synthetic_plans(args.synthetic, random.Random(SEED)))
    x, y = collect(plans, args.per_type)
    if len(y) < 100:
        raise SystemExit(f"Слишком мало примеров для обучения: {len(y)}")
    model = train(x, y)
    Path(args.out).write_text(json.dumps(model, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Примеров: {model['samples']}, метрики: {model['metrics']}")
    print("Влияние признаков:", sorted(model["weights"].items(), key=lambda kv: -abs(kv[1])))


if __name__ == "__main__":
    main()
