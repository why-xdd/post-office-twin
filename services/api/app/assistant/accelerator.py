"""Обучаемый ускоритель поиска места.

Полная проверка кандидата стоит дорого: правила считают проходимость, зоны разворота и
площади. Поэтому проверяется только верхушка списка, а порядок задаёт оценка уместности —
набор правил, написанных вручную.

Здесь этот порядок уточняет модель, обученная на данных, которые сервис порождает сам:
для тысяч позиций известно, чем закончилась полная проверка. Модель — логистическая регрессия
на признаках позиции (расстояния до стен, входа, соседей, площадь, поворот). Она маленькая,
считается за микросекунды, обучается офлайн и объяснима: у каждого признака есть вес, который
можно прочитать и обсудить с проверяющим.

Файл весов необязателен: без него поиск работает как раньше, только медленнее.
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
)


@dataclass(frozen=True)
class Model:
    """Логистическая регрессия: вероятность того, что позиция пройдёт полную проверку."""

    weights: dict[str, float]
    bias: float
    mean: dict[str, float]
    std: dict[str, float]
    metrics: dict[str, float]
    trained_at: str
    samples: int

    def probability(self, features: dict[str, float]) -> float:
        z = self.bias
        for name in FEATURES:
            value = float(features.get(name, 0.0))
            z += self.weights.get(name, 0.0) * (value - self.mean.get(name, 0.0)) / (self.std.get(name, 1.0) or 1.0)
        return 1.0 / (1.0 + math.exp(-max(-30.0, min(30.0, z))))

    def explain(self) -> list[tuple[str, float]]:
        """Признаки по убыванию влияния — для README и разговора с проверяющим."""
        return sorted(self.weights.items(), key=lambda item: -abs(item[1]))


_lock = threading.Lock()
_loaded = False
_model: Model | None = None


def load(path: Path | None = None) -> Model | None:
    """Модель из файла весов. Загрузка однократна и потокобезопасна: обработчики живут в пуле потоков."""
    global _loaded, _model
    target = path or MODEL_PATH
    with _lock:
        if _loaded and path is None:
            return _model
        model: Model | None = None
        if target.exists():
            try:
                data = json.loads(target.read_text(encoding="utf-8"))
                model = Model(
                    weights={k: float(v) for k, v in data["weights"].items()},
                    bias=float(data["bias"]),
                    mean={k: float(v) for k, v in data["mean"].items()},
                    std={k: float(v) for k, v in data["std"].items()},
                    metrics={k: float(v) for k, v in data.get("metrics", {}).items()},
                    trained_at=str(data.get("trained_at", "")),
                    samples=int(data.get("samples", 0)),
                )
            except (ValueError, KeyError, OSError):
                # Битый файл весов не должен ронять сервис: поиск просто работает без модели.
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
