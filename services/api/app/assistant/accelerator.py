"""Обучаемый ускоритель поиска места.

Полная проверка кандидата стоит дорого: правила считают проходимость, зоны разворота и
площади. Поэтому проверяется только верхушка списка, а порядок задаёт оценка уместности —
набор правил, написанных вручную.

Здесь этот порядок уточняет модель, обученная на данных, которые сервис порождает сам: для сотен
тысяч позиций известно и то, чем закончилась полная проверка, и то, какой получилась расстановка
целиком. Поэтому голов две. Первая отвечает «пройдёт ли позиция проверку» — это отсев заведомо
плохих мест. Вторая предсказывает величину цели: среди допустимых мест она отличает удачное от
просто терпимого. Одной первой мало: допустимых мест обычно большинство, и без второй головы
порядок внутри них случаен.

Обе головы — градиентный бустинг на решающих деревьях: для табличных признаков он точнее линейной
модели, а вывод — обход неглубоких деревьев, то есть микросекунды на кандидата.

Сервис не зависит ни от одной библиотеки машинного обучения: деревья лежат в JSON, обход написан
здесь, обучение живёт в scripts/ и в поставку не входит. Файл весов необязателен: без него поиск
работает как раньше, только менее прицельно. Вторая голова тоже необязательна — файл, где её нет,
читается по-прежнему.
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
    "route_distance",
    "route_hits",
    "access_distance",
    "route_pinch",
    "entrance_relative",
    "in_waiting",
    "in_queue_accumulator",
    "in_vestibule",
    "in_back_office",
    "in_staff_room",
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
class Quality:
    """Вторая голова: насколько хорошей будет расстановка, если объект встанет сюда.

    Классификатор «место войдёт в лучшие» (loss="logistic") и голова, обученная на порядок
    (loss="lambdarank"), отвечают «больше — лучше», регрессия на величину цели — «меньше —
    лучше». Наружу отдаётся всегда «меньше — лучше», чтобы порядок проверки не зависел от того,
    как голова обучена.
    """

    trees: list[Tree]
    init: float
    learning_rate: float
    importance: dict[str, float]
    loss: str = "squared"

    def raw(self, row: list[float]) -> float:
        return self.init + self.learning_rate * sum(tree.predict(row) for tree in self.trees)

    def value(self, row: list[float]) -> float:
        raw = self.raw(row)
        return -raw if self.loss in ("logistic", "lambdarank") else raw

    def explain(self) -> list[tuple[str, float]]:
        return sorted(self.importance.items(), key=lambda item: -item[1])


@dataclass(frozen=True)
class Model:
    """Ансамбль деревьев: вероятность «позиция пройдёт проверку» и ожидаемое качество места."""

    trees: list[Tree]
    init: float
    learning_rate: float
    importance: dict[str, float]
    metrics: dict[str, float]
    trained_at: str
    samples: int
    features: tuple[str, ...] = FEATURES
    quality: Quality | None = None

    def row(self, features: dict[str, float]) -> list[float]:
        return [float(features.get(name, 0.0)) for name in self.features]

    def probability(self, features: dict[str, float]) -> float:
        # Строка признаков собирается один раз на позицию: в ансамбле сто с лишним деревьев.
        row = self.row(features)
        raw = self.init + self.learning_rate * sum(tree.predict(row) for tree in self.trees)
        return 1.0 / (1.0 + math.exp(-max(-30.0, min(30.0, raw))))

    def expected_score(self, features: dict[str, float]) -> float:
        """Ожидаемая величина цели для места. Ноль, если вторая голова не обучена."""
        return self.quality.value(self.row(features)) if self.quality is not None else 0.0

    def explain(self) -> list[tuple[str, float]]:
        """Признаки по убыванию влияния."""
        return sorted(self.importance.items(), key=lambda item: -item[1])


_lock = threading.Lock()
_loaded = False
_model: Model | None = None


def _trees(items: list[dict]) -> list[Tree]:
    return [
        Tree(
            feature=[int(v) for v in tree["feature"]],
            threshold=[float(v) for v in tree["threshold"]],
            left=[int(v) for v in tree["left"]],
            right=[int(v) for v in tree["right"]],
            value=[float(v) for v in tree["value"]],
        )
        for tree in items
    ]


def _parse(data: dict) -> Model:
    trees = _trees(data["trees"])
    quality = None
    if isinstance(data.get("quality"), dict):
        head = data["quality"]
        quality = Quality(
            trees=_trees(head["trees"]),
            init=float(head["init"]),
            learning_rate=float(head.get("learning_rate", 1.0)),
            importance={k: float(v) for k, v in head.get("importance", {}).items()},
            loss=str(head.get("loss", "squared")),
        )
    return Model(
        trees=trees,
        quality=quality,
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


def rank_pairs(candidates: list[dict[str, float]]) -> list[tuple[float, float]] | None:
    """Пары «вероятность пройти проверку, ожидаемая величина цели», либо None без модели.

    Второе число равно нулю, если в файле нет второй головы: тогда порядок определяется только
    вероятностью, как в прежней версии.
    """
    model = load()
    if model is None:
        return None
    return [(model.probability(features), model.expected_score(features)) for features in candidates]
