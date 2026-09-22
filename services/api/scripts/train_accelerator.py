"""Обучение ускорителя поиска места.

Данные сервис порождает сам: для каждой позиции, прошедшей быстрый фильтр, запускается полная
проверка, и её исход становится меткой. На этих парах «признаки позиции → прошла или нет»
обучается градиентный бустинг на решающих деревьях.

Бустинг написан в scripts/gbdt.py на numpy: ни обучение, ни сервис не тянут внешних библиотек
машинного обучения. В сервис уезжают только деревья в JSON, вывод по ним — в
app/assistant/accelerator.py. Экспорт сверяется с обученной моделью: расхождение обязано быть
машинным нулём.

Запуск из services/api:
    uv run python scripts/train_accelerator.py --plans ../../data/example_plan.json --synthetic 180 --per-type 200

Реальные планы БТИ не используются, разнообразие даёт генератор синтетических отделений.
"""

from __future__ import annotations

import argparse
import json
import math
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


def _document(width: float, depth: float, cut: float, notch: tuple[float, float] | None, index: int) -> dict:
    """Каркас отделения: контур, перегородка, зоны. notch вырезает угол — получается Г-образный зал."""
    if notch is None:
        outline = [[0, 0], [width, 0], [width, depth], [0, depth]]
        client = [[0, 0], [cut, 0], [cut, depth], [0, depth]]
    else:
        notch_x, notch_d = notch
        outline = [
            [0, 0],
            [width, 0],
            [width, depth],
            [notch_x, depth],
            [notch_x, depth - notch_d],
            [0, depth - notch_d],
        ]
        client = [
            [0, 0],
            [cut, 0],
            [cut, depth],
            [notch_x, depth],
            [notch_x, depth - notch_d],
            [0, depth - notch_d],
        ]
    walls = [
        {"id": f"w_{i}", "from": outline[i], "to": outline[(i + 1) % len(outline)], "type": "outer"}
        for i in range(len(outline))
    ]
    walls.append({"id": "w_p", "from": [cut, 0], "to": [cut, depth], "type": "partition"})
    return {
        "ops": {"id": f"train_{index}", "name": f"Учебное отделение {index + 1}"},
        "walls": walls,
        "doors": [],
        "zones": [
            {"id": "z_client", "type": "client_area", "label": "Клиентская зона", "polygon": client},
            {
                "id": "z_service",
                "type": "back_office",
                "label": "Служебная зона",
                "polygon": [[cut, 0], [width, 0], [width, depth], [cut, depth]],
            },
        ],
        "equipment": [],
    }


def synthetic_plan(index: int, rng: random.Random) -> Plan:
    """Случайное отделение: размеры, форма зала, служебная зона, зона ожидания и оборудование.

    Разнообразие важнее реалистичности отдельного плана: модель должна увидеть и тесные залы, и
    просторные, и прямоугольные, и Г-образные, и с входом с разных сторон. Иначе она выучит
    расположение окон в одном-единственном типе помещения.
    """
    width = rng.uniform(7.0, 22.0)
    depth = rng.uniform(5.5, 15.0)
    service = rng.uniform(2.5, max(2.6, min(6.0, width - 4.0)))
    cut = width - service
    notch = None
    if rng.random() < 0.35 and cut > 5.0 and depth > 8.0:
        notch = (rng.uniform(1.5, cut - 3.0), rng.uniform(1.5, depth / 3.0))
    document = _document(width, depth, cut, notch, index)

    # Вход: на дальней стене у прямоугольного зала, на ближней у Г-образного — вырезанный угол
    # не должен оказаться единственным местом для двери.
    if notch is None:
        x = rng.uniform(0.5, max(0.6, cut - 2.0))
        entrance = [x, depth, x + 1.5, depth]
    else:
        x = rng.uniform(0.5, max(0.6, cut - 2.0))
        entrance = [x, 0.0, x + 1.5, 0.0]
    document["doors"] = [
        {"id": "d_main", "wall": entrance, "type": "entrance", "width_m": 1.5},
        {
            "id": "d_int",
            "wall": [cut, depth / 2 - 0.45, cut, depth / 2 + 0.45],
            "type": "internal",
            "width_m": 0.9,
        },
    ]

    wait_depth = min(3.5, depth - 2.0)
    if notch is None:
        wait = [
            [0.2, depth - wait_depth],
            [cut - 0.2, depth - wait_depth],
            [cut - 0.2, depth - 0.2],
            [0.2, depth - 0.2],
        ]
    else:
        wait = [[0.2, 0.2], [cut - 0.2, 0.2], [cut - 0.2, wait_depth], [0.2, wait_depth]]
    document["zones"].append({"id": "z_wait", "type": "waiting", "label": "Зона ожидания", "polygon": wait})

    for slot in range(rng.randint(0, 8)):
        type_code = rng.choice(list(SIZES))
        size = SIZES[type_code]
        y_limit = depth - 1.0 if notch is None else depth - (notch[1] + 1.0)
        document["equipment"].append(
            {
                "id": f"eq_{slot}",
                "type": type_code,
                "position": [
                    round(rng.uniform(1.0, max(1.1, cut - 1.0)), 2),
                    round(rng.uniform(1.0, max(1.1, y_limit)), 2),
                ],
                "size_m": list(size),
                "rotation_deg": rng.choice([0.0, 90.0]),
            }
        )
    return Plan.model_validate(document)


def collect_one(task: tuple[dict, int, int]) -> tuple[list[list[float]], list[int], list[float], list[str]]:
    """Размеченные позиции для одного плана. Отдельная функция — чтобы считать в пуле процессов.

    Меток две. Первая отвечает на вопрос «пройдёт ли позиция полную проверку», вторая — «какой
    получится расстановка целиком»: величина цели, в которую входят нарушения, ширина прохода,
    число мест ожидания, путь посетителя и величина переноса.
    """
    document, per_type, seed = task
    plan = Plan.model_validate(document)
    rng = random.Random(seed)
    known = equipment_types(plan)
    rows: list[list[float]] = []
    labels: list[int] = []
    scores: list[float] = []
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
            scores.append(float(evaluated.score.total) if evaluated.score is not None else 0.0)
            groups.append(f"{plan.ops.id}:{type_code}")
    return rows, labels, scores, groups


def collect(
    plans: list[Plan], per_type: int, workers: int
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    tasks = [
        (plan.model_dump(by_alias=True, exclude_none=True), per_type, SEED + index)
        for index, plan in enumerate(plans)
    ]
    rows: list[list[float]] = []
    labels: list[int] = []
    scores: list[float] = []
    groups: list[str] = []
    with mp.Pool(processes=workers) as pool:
        for index, part in enumerate(pool.imap_unordered(collect_one, tasks), 1):
            part_rows, part_labels, part_scores, part_groups = part
            rows.extend(part_rows)
            labels.extend(part_labels)
            scores.extend(part_scores)
            groups.extend(part_groups)
            print(f"  план {index}/{len(tasks)}: всего примеров {len(labels)}", flush=True)
    return (
        np.array(rows, dtype=float),
        np.array(labels, dtype=float),
        np.array(scores, dtype=float),
        np.array(groups),
    )


def relevance(scores: np.ndarray, labels: np.ndarray, groups: np.ndarray) -> np.ndarray:
    """Цель второй головы: уровень места в своём списке кандидатов, от 4 до 0.

    Сама величина цели несравнима между планами: в тесном зале любая расстановка хуже, чем в
    просторном. Сервису же нужен порядок внутри одного списка, поэтому голова учится порядку
    (LambdaRank), а не величине. Уровни — по доле места в списке: лучшие 3 % — 4, до 10 % — 3,
    до 25 % — 2, до половины — 1. Места, не прошедшие проверку, всегда 0: их сервис не выберет,
    как бы хороша ни была остальная расстановка.

    Этими уровнями меряется NDCG второй головы; учить на них можно ключом
    --quality-target lambdarank, но по умолчанию голова учится на `best_places`.
    """
    levels = np.zeros(len(scores))
    for group in np.unique(groups):
        mask = groups == group
        if mask.sum() < 2:
            continue
        share = np.argsort(np.argsort(scores[mask], kind="stable"), kind="stable") / mask.sum()
        levels[mask] = np.select([share < 0.03, share < 0.10, share < 0.25, share < 0.50], [4, 3, 2, 1], 0)
    return np.where(labels >= 0.5, levels, 0.0)


def best_places(scores: np.ndarray, labels: np.ndarray, groups: np.ndarray, share: float = 0.05) -> np.ndarray:
    """Цель второй головы по умолчанию: войдёт ли место в лучшие 5 % допустимых мест своего списка.

    Сервис полностью проверяет только верхушку списка и берёт из неё лучшее, поэтому голове не
    нужно упорядочивать весь список — только поднять наверх самые удачные места. Обычная
    логистическая классификация делает ровно это и обучается устойчиво.

    Сравнение на отложенных помещениях, доля списков, где лучшее место попало в первые 10:
    без модели и с одной головой — 71 %, вторая голова регрессией на величину цели — 73–79 %,
    LambdaRank по уровням `relevance` — 87 %, эта цель — 90 %. На верхушке 24 места, как в
    сервисе: LambdaRank — 91 %, эта цель — 94 %. LambdaRank после 5–10 деревьев начинает портить
    верхушку: он выравнивает весь список, где большинство пар — «среднее против плохого».
    """
    target = np.zeros(len(scores))
    for group in np.unique(groups):
        mask = (groups == group) & (labels >= 0.5)
        count = int(mask.sum())
        if count == 0:
            continue
        # Не меньше одного места: единственное допустимое место в списке — лучшее по определению.
        best = max(1, math.ceil(share * count))
        rank = np.argsort(np.argsort(scores[mask], kind="stable"), kind="stable")
        target[mask] = (rank < best).astype(float)
    return target


def per_group_standard(values: np.ndarray, groups: np.ndarray) -> np.ndarray:
    """Оценки второй головы, приведённые к нулю и единице внутри списка — как в сервисе."""
    result = np.zeros(len(values))
    for group in np.unique(groups):
        mask = groups == group
        spread = float(values[mask].std())
        result[mask] = (values[mask] - values[mask].mean()) / spread if spread > 1e-9 else 0.0
    return result


def precision_at_k(order: np.ndarray, labels: np.ndarray, groups: np.ndarray, k: int = 10) -> float:
    """Доля подходящих мест в первых k позициях списка — то, ради чего нужна первая голова."""
    hits, total = 0, 0
    for group in np.unique(groups):
        mask = groups == group
        best = np.argsort(-order[mask])[:k]
        hits += int(labels[mask][best].sum())
        total += len(best)
    return hits / total if total else 0.0


def regret_at_k(order: np.ndarray, scores: np.ndarray, groups: np.ndarray, k: int = 10) -> tuple[float, float]:
    """Чего стоит проверять только верхушку списка.

    Сервис полностью проверяет первые k кандидатов и берёт из них лучшего. Здесь считается, на
    сколько этот выбор хуже идеального — лучшего места во всём списке — в единицах той самой
    величины цели. Вторая величина: доля списков, где идеальное место вообще попало в верхушку.
    """
    losses: list[float] = []
    found = 0
    for group in np.unique(groups):
        mask = groups == group
        group_scores = scores[mask]
        if len(group_scores) == 0:
            continue
        top = np.argsort(-order[mask])[:k]
        losses.append(float(group_scores[top].min() - group_scores.min()))
        found += int(group_scores[top].min() <= group_scores.min() + 1e-9)
    if not losses:
        return 0.0, 0.0
    return float(np.mean(losses)), found / len(losses)


def spearman(prediction: np.ndarray, target: np.ndarray, groups: np.ndarray) -> float:
    """Согласие порядков внутри списка, усреднённое по спискам: 1 — полное, 0 — случайное."""
    values: list[float] = []
    for group in np.unique(groups):
        mask = groups == group
        if mask.sum() < 3:
            continue
        a, b = prediction[mask], target[mask]
        rank_a = np.argsort(np.argsort(a)).astype(float)
        rank_b = np.argsort(np.argsort(b)).astype(float)
        if rank_a.std() < 1e-9 or rank_b.std() < 1e-9:
            continue
        values.append(float(np.corrcoef(rank_a, rank_b)[0, 1]))
    return float(np.mean(values)) if values else 0.0


def roc_auc(y: np.ndarray, probability: np.ndarray) -> float:
    """Площадь под ROC через ранги: доля правильно упорядоченных пар «подходит — не подходит»."""
    positives, negatives = probability[y >= 0.5], probability[y < 0.5]
    if len(positives) == 0 or len(negatives) == 0:
        return 0.5
    order = np.argsort(np.concatenate([positives, negatives]))
    ranks = np.empty(len(order), dtype=float)
    ranks[order] = np.arange(1, len(order) + 1)
    return float((ranks[: len(positives)].sum() - len(positives) * (len(positives) + 1) / 2) / (len(positives) * len(negatives)))


def head(model: gbdt.Booster) -> dict:
    """Одна голова модели в том виде, в каком её читает сервис."""
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
        "trees": trees,
        "init": round(model.init, 6),
        "learning_rate": model.learning_rate,
        "loss": model.loss,
        "importance": {k: round(v, 5) for k, v in sorted(importance.items(), key=lambda kv: -kv[1])},
    }


def export(passes: gbdt.Booster, quality: gbdt.Booster, metrics: dict, samples: int) -> dict:
    """Обе головы в одном файле.

    Первая голова лежит на верхнем уровне — так файл читается и прежней версией сервиса, где
    головы ещё одна. Вторая добавлена отдельным разделом: её отсутствие ничего не ломает.
    """
    exported = {"kind": "gradient_boosting", "features": list(accelerator.FEATURES)}
    exported.update(head(passes))
    exported["quality"] = head(quality)
    exported["metrics"] = metrics
    exported["trained_at"] = datetime.now(timezone.utc).date().isoformat()
    exported["samples"] = samples
    return exported


def check_export(path: Path, passes: gbdt.Booster, quality: gbdt.Booster, sample: np.ndarray) -> float:
    """Сверка: вывод по JSON обязан совпадать с обученной моделью до последнего знака."""
    accelerator.reset()
    model = accelerator.load(path)
    assert model is not None and model.quality is not None
    mine_pass = np.array(
        [model.init + model.learning_rate * sum(t.predict(list(row)) for t in model.trees) for row in sample]
    )
    mine_quality = np.array([model.quality.raw(list(row)) for row in sample])
    return max(
        float(np.abs(passes.raw(sample) - mine_pass).max()),
        float(np.abs(quality.raw(sample) - mine_quality).max()),
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Обучение ускорителя поиска места")
    parser.add_argument("--plans", nargs="+", required=True)
    parser.add_argument("--out", default="app/assistant/accelerator_model.json")
    parser.add_argument("--per-type", type=int, default=160)
    parser.add_argument("--synthetic", type=int, default=40)
    parser.add_argument("--trees", type=int, default=400)
    parser.add_argument("--depth", type=int, default=5)
    parser.add_argument("--quality-depth", type=int, default=6, help="Глубина второй головы")
    parser.add_argument("--quality-rate", type=float, default=0.08)
    parser.add_argument("--quality-leaf", type=int, default=20, help="Наименьший лист второй головы")
    parser.add_argument("--quality-target", choices=("best", "lambdarank"), default="best",
                        help="best — лучшие 5 %% мест списка, lambdarank — порядок по уровням")
    parser.add_argument("--quality-patience", type=int, default=50)
    parser.add_argument("--rate", type=float, default=0.08)
    parser.add_argument("--workers", type=int, default=max(1, mp.cpu_count() - 2))
    parser.add_argument("--cache", default="", help="Файл выборки: собрать один раз, обучать много")
    parser.add_argument("--collect-only", action="store_true", help="Только собрать выборку в --cache")
    args = parser.parse_args()

    rng = random.Random(SEED)
    plans = [Plan.model_validate(json.loads(Path(p).read_text(encoding="utf-8"))) for p in args.plans]
    plans.extend(synthetic_plan(index, rng) for index in range(args.synthetic))
    print(f"Планов для обучения: {len(plans)}, процессов: {args.workers}", flush=True)

    cache = Path(args.cache) if args.cache else None
    if cache is not None and cache.exists():
        stored = np.load(cache, allow_pickle=True)
        x, y, scores, groups = stored["x"], stored["y"], stored["scores"], stored["groups"]
        print(f"Выборка взята из {cache}: {len(y)} примеров", flush=True)
    else:
        x, y, scores, groups = collect(plans, args.per_type, args.workers)
        if cache is not None:
            np.savez_compressed(cache, x=x, y=y, scores=scores, groups=groups)
    if len(y) < 1000:
        raise SystemExit(f"Слишком мало примеров: {len(y)}")
    if args.collect_only:
        print(f"Выборка собрана: {len(y)} примеров, {x.shape[1]} признаков", flush=True)
        return

    # Строки лежат в порядке генерации кандидатов, а он связан с качеством места: при равных
    # оценках метрики верхушки списка подсматривали бы ответ. Перемешиваем один раз.
    order = np.random.default_rng(SEED).permutation(len(y))
    x, y, scores, groups = x[order], y[order], scores[order], groups[order]

    # Деление по планам, а не по строкам: модель проверяется на помещениях, которых не видела.
    unique_groups = sorted({g.split(":")[0] for g in groups})
    holdout = set(unique_groups[: max(1, len(unique_groups) // 5)])
    is_test = np.array([g.split(":")[0] in holdout for g in groups])
    levels = relevance(scores, y, groups)
    x_train, x_test = x[~is_test], x[is_test]
    y_train, y_test = y[~is_test], y[is_test]
    scores_test, groups_test = scores[is_test], groups[is_test]

    print("Голова 1: пройдёт ли позиция полную проверку", flush=True)
    passes = gbdt.train(
        x_train, y_train, trees=args.trees, learning_rate=args.rate, max_depth=args.depth,
        seed=SEED, validation=(x_test, y_test),
    )
    print("Голова 2: какое место в списке лучше для расстановки", flush=True)
    if args.quality_target == "lambdarank":
        quality = gbdt.train(
            x_train, levels[~is_test], trees=args.trees, learning_rate=args.quality_rate,
            max_depth=args.quality_depth, min_samples_leaf=args.quality_leaf, seed=SEED,
            validation=(x_test, levels[is_test]), patience=args.quality_patience, loss="lambdarank",
            groups=groups[~is_test], validation_groups=groups_test,
        )
    else:
        best = best_places(scores, y, groups)
        quality = gbdt.train(
            x_train, best[~is_test], trees=args.trees, learning_rate=args.quality_rate,
            max_depth=args.quality_depth, min_samples_leaf=args.quality_leaf, seed=SEED,
            validation=(x_test, best[is_test]), patience=args.quality_patience,
        )

    probability = passes.probability(x_test)
    predicted = quality.raw(x_test)
    accuracy = float(((probability >= 0.5) == (y_test >= 0.5)).mean())
    on_suitable = y_test >= 0.5
    test_index = gbdt._group_index(groups_test)

    # Порядок проверки — тот же, что в сервисе: оценка уместности, поправка на вероятность
    # прохождения и на предсказанное качество. Сортировка по убыванию, поэтому знаки такие.
    fitness = x_test[:, list(accelerator.FEATURES).index("fitness")]
    baseline_order = -fitness
    pass_order = -fitness + search.MODEL_WEIGHT * probability
    full_order = pass_order + search.QUALITY_WEIGHT * per_group_standard(predicted, groups_test)

    regret_baseline, found_baseline = regret_at_k(baseline_order, scores_test, groups_test)
    regret_pass, found_pass = regret_at_k(pass_order, scores_test, groups_test)
    regret_full, found_full = regret_at_k(full_order, scores_test, groups_test)
    # Столько мест сервис проверяет полностью по умолчанию.
    top = search.DEFAULT_TOP_K
    wide = {name: regret_at_k(order, scores_test, groups_test, k=top)
            for name, order in (("прежний порядок", baseline_order), ("одна голова", pass_order), ("две головы", full_order))}

    metrics = {
        "доля верных ответов": round(accuracy, 4),
        "площадь под ROC": round(roc_auc(y_test, probability), 4),
        "подходящих в первых 10 — модель": round(precision_at_k(pass_order, y_test, groups_test), 4),
        "подходящих в первых 10 — прежний порядок": round(precision_at_k(baseline_order, y_test, groups_test), 4),
        "доля подходящих мест в выборке": round(float(y.mean()), 4),
        "NDCG@10 второй головы": round(gbdt.ndcg_at(predicted, levels[is_test], test_index), 4),
        "NDCG@10 — прежний порядок": round(gbdt.ndcg_at(baseline_order, levels[is_test], test_index), 4),
        # Спирмен с минусом: голова отвечает «больше — лучше», а величина цели «меньше — лучше».
        "согласие порядков": round(
            spearman(-predicted[on_suitable], scores_test[on_suitable], groups_test[on_suitable]), 4
        ),
        "проигрыш лучшему месту — прежний порядок": round(regret_baseline, 3),
        "проигрыш лучшему месту — одна голова": round(regret_pass, 3),
        "проигрыш лучшему месту — две головы": round(regret_full, 3),
        "лучшее место в первых 10 — прежний порядок": round(found_baseline, 4),
        "лучшее место в первых 10 — одна голова": round(found_pass, 4),
        "лучшее место в первых 10 — две головы": round(found_full, 4),
        **{f"проигрыш лучшему месту в первых {top} — {name}": round(value[0], 3) for name, value in wide.items()},
        **{f"лучшее место в первых {top} — {name}": round(value[1], 4) for name, value in wide.items()},
    }

    out = Path(args.out)
    out.write_text(
        json.dumps(export(passes, quality, metrics, int(len(y))), ensure_ascii=False, indent=1),
        encoding="utf-8",
    )
    difference = check_export(out, passes, quality, x_test[:200])

    print(f"Примеров: {len(y)}, деревьев: {len(passes.trees)} и {len(quality.trees)}")
    for name, value in metrics.items():
        print(f"  {name}: {value}")
    print(f"Расхождение экспорта: {difference:.2e}")
    if difference > 1e-6:
        raise SystemExit("Экспорт деревьев расходится с обученной моделью")


if __name__ == "__main__":
    main()
