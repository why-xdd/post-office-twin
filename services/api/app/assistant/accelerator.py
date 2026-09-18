"""Обучаемый ускоритель поиска места.

Полная проверка кандидата стоит дорого: правила считают проходимость, зоны разворота и
площади. Поэтому проверяется только верхушка списка, а порядок задаёт оценка уместности —
набор правил, написанных вручную.

Здесь этот порядок уточняет модель, обученная на данных, которые сервис порождает сам: для
десятков тысяч позиций известно, чем закончилась полная проверка. Модель — градиентный бустинг
на решающих деревьях: для табличных признаков он точнее линейной модели, а вывод — обход
неглубоких деревьев, то есть микросекунды на кандидата.

Сервис не зависит ни от одной библиотеки машинного обучения: деревья лежат в JSON, обход
написан здесь. Обучение (scikit-learn) живёт в scripts/train_accelerator.py и в поставку не
входит. Файл весов необязателен: без него поиск работает как раньше, только менее прицельно.
"""

from __future__ import annotations

import json
import math
import threading
from dataclasses import dataclass
from pathlib import Path

MODEL_PATH = Path(__file__).with_name("accelerator_model.json")

FEATURES = (
    "fitness",
    "wall_distance",
    "entrance_distance",
    "nearest_obstacle",
    "rotation",
    "area",
    "boundary_distance",
    "door_distance",
    "width",
    "depth",
    "aspect",
    "partition_distance",
    "window_distance",
    "entrance_axis_distance",
    "free_around",
    "zone_public",
    "zone_area",
    "zone_free_ratio",
    "plan_area",
    "plan_objects",
    "plan_density",
    "region_area",
    "floor_object",
    "operator_workplace",
    "type_cashier_window",
    "type_pvz_counter",
    "type_storage_rack",
    "type_waiting_seat",
    "type_self_service_kiosk",
    "type_atm",
    "type_info_stand",
    "type_package_drop_box",
)


@dataclass(frozen=True)
class Tree:
    """Решающее дерево в массивах: −1 в feature означает лист."""

    feature: list[int]
    threshold: list[float]
    left: list[int]
    right: list[int]
    value: list[float]

    def predict(self, row: list[float]) -> float:
        node = 0
        while self.feature[node] >= 0:
            node = self.left[node] if row[self.feature[node]] <= self.threshold[node] else self.right[node]
        return self.value[node]


@dataclass(frozen=True)
class Model:
    """Ансамбль деревьев, предсказывающий вероятность «позиция пройдёт полную проверку»."""

    trees: list[Tree]
    init: float
    learning_rate: float
    importance: dict[str, float]
    metrics: dict[str, float]
    trained_at: str
    samples: int
    features: tuple[str, ...] = FEATURES

    def row(self, features: dict[str, float]) -> list[float]:
        return [float(features.get(name, 0.0)) for name in self.features]

    def probability(self, features: dict[str, float]) -> float:
        raw = self.init + self.learning_rate * sum(tree.predict(self.row(features)) for tree in self.trees)
        return 1.0 / (1.0 + math.exp(-max(-30.0, min(30.0, raw))))

    def explain(self) -> list[tuple[str, float]]:
        """Признаки по убыванию влияния — для README и разговора с проверяющим."""
        return sorted(self.importance.items(), key=lambda item: -item[1])


_lock = threading.Lock()
_loaded = False
_model: Model | None = None


def _parse(data: dict) -> Model:
    trees = [
        Tree(
            feature=[int(v) for v in tree["feature"]],
            threshold=[float(v) for v in tree["threshold"]],
            left=[int(v) for v in tree["left"]],
            right=[int(v) for v in tree["right"]],
            value=[float(v) for v in tree["value"]],
        )
        for tree in data["trees"]
    ]
    return Model(
        trees=trees,
        init=float(data["init"]),
        learning_rate=float(data.get("learning_rate", 1.0)),
        importance={k: float(v) for k, v in data.get("importance", {}).items()},
        metrics={k: float(v) for k, v in data.get("metrics", {}).items()},
        trained_at=str(data.get("trained_at", "")),
        samples=int(data.get("samples", 0)),
        features=tuple(data.get("features", FEATURES)),
    )


def load(path: Path | None = None) -> Model | None:
    """Модель из файла. Загрузка однократна и потокобезопасна: обработчики живут в пуле потоков."""
    global _loaded, _model
    target = path or MODEL_PATH
    with _lock:
        if _loaded and path is None:
            return _model
        model: Model | None = None
        if target.exists():
            try:
                model = _parse(json.loads(target.read_text(encoding="utf-8")))
            except (ValueError, KeyError, TypeError, OSError):
                # Битый файл не должен ронять сервис: поиск просто работает без модели.
                model = None
        if path is None:
            _model, _loaded = model, True
        return model


def reset() -> None:
    """Сброс кэша модели — нужен тестам и после переобучения."""
    global _loaded, _model
    with _lock:
        _loaded, _model = False, None


def rank(candidates: list[dict[str, float]]) -> list[float] | None:
    """Вероятности «позиция пройдёт проверку» для списка признаков, либо None без модели."""
    model = load()
    if model is None:
        return None
    return [model.probability(features) for features in candidates]
