"""Градиентный бустинг на решающих деревьях — обучение без внешних библиотек.

Здесь две задачи. Первая — предсказать, пройдёт ли позиция полную проверку: бинарная
классификация с логистической функцией потерь. Вторая — предсказать, насколько хороша будет
расстановка, если объект встанет сюда: регрессия с квадратичной функцией потерь. Обе решает один
и тот же код, разница только в градиенте, гессиане и начальном приближении.

Это табличные данные, где бустинг обычно сильнее и линейной модели, и нейросети.

Как это работает. Модель — сумма неглубоких деревьев. Сначала берётся общее начальное
приближение (логит доли подходящих мест либо среднее значение цели), затем каждое следующее
дерево учится на ошибках предыдущих: для логистической функции потерь градиент g = p − y и
гессиан h = p (1 − p), для квадратичной — g = f − y и h = 1. Дерево ищет разбиение, максимально
уменьшающее потери; значение листа — −Σg / (Σh + λ), как в известных реализациях бустинга.

Скорость даёт предварительное разбиение каждого признака на 256 корзин: вместо перебора всех
порогов достаточно накопить суммы по корзинам. Это тот же приём, что и в гистограммных
реализациях.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

MAX_BINS = 256


@dataclass
class TreeArrays:
    """Дерево в массивах: −1 в feature означает лист."""

    feature: list[int] = field(default_factory=list)
    threshold: list[float] = field(default_factory=list)
    left: list[int] = field(default_factory=list)
    right: list[int] = field(default_factory=list)
    value: list[float] = field(default_factory=list)

    def add_node(self) -> int:
        self.feature.append(-1)
        self.threshold.append(0.0)
        self.left.append(-1)
        self.right.append(-1)
        self.value.append(0.0)
        return len(self.feature) - 1

    def predict(self, x: np.ndarray) -> np.ndarray:
        feature = np.array(self.feature)
        threshold = np.array(self.threshold)
        left = np.array(self.left)
        right = np.array(self.right)
        value = np.array(self.value)
        node = np.zeros(len(x), dtype=int)
        active = feature[node] >= 0
        while active.any():
            rows = np.nonzero(active)[0]
            current = node[rows]
            go_left = x[rows, feature[current]] <= threshold[current]
            node[rows] = np.where(go_left, left[current], right[current])
            active = feature[node] >= 0
        return value[node]


@dataclass
class Booster:
    trees: list[TreeArrays]
    init: float
    learning_rate: float
    importance: np.ndarray
    loss: str = "logistic"

    def raw(self, x: np.ndarray) -> np.ndarray:
        total = np.full(len(x), self.init)
        for tree in self.trees:
            total += self.learning_rate * tree.predict(x)
        return total

    def probability(self, x: np.ndarray) -> np.ndarray:
        return 1.0 / (1.0 + np.exp(-np.clip(self.raw(x), -30, 30)))

    def predict(self, x: np.ndarray) -> np.ndarray:
        """Ответ модели в том виде, в каком его читает человек: вероятность или число."""
        return self.raw(x) if self.loss == "squared" else self.probability(x)


def _group_index(groups: np.ndarray) -> list[np.ndarray]:
    """Номера строк каждого списка кандидатов."""
    order = np.argsort(groups, kind="stable")
    sorted_groups = groups[order]
    cuts = np.flatnonzero(sorted_groups[1:] != sorted_groups[:-1]) + 1
    return [part for part in np.split(order, cuts) if len(part) > 1]


def _ideal_dcg(gains: np.ndarray, k: int) -> float:
    top = np.sort(gains)[::-1][:k]
    return float((top / np.log2(np.arange(2, len(top) + 2))).sum())


def lambda_gradients(raw: np.ndarray, relevance: np.ndarray, index: list[np.ndarray]) -> tuple[np.ndarray, np.ndarray]:
    """Градиенты LambdaRank: пары «лучше — хуже» внутри одного списка.

    Для каждой пары, где первое место объективно лучше второго, модель получает толчок поднять
    первое и опустить второе. Сила толчка — насколько перестановка этой пары изменила бы NDCG
    всего списка: ошибки в верхушке стоят дороже, чем в хвосте.
    """
    gradient = np.zeros(len(raw))
    hessian = np.zeros(len(raw))
    for rows in index:
        s = raw[rows]
        r = relevance[rows]
        gains = 2.0**r - 1.0
        idcg = _ideal_dcg(gains, len(rows))
        if idcg <= 0:
            continue
        rank = np.empty(len(rows), dtype=int)
        rank[np.argsort(-s, kind="stable")] = np.arange(len(rows))
        discount = 1.0 / np.log2(rank + 2.0)
        better = r[:, None] > r[None, :]
        delta = np.abs((gains[:, None] - gains[None, :]) * (discount[:, None] - discount[None, :])) / idcg
        rho = 1.0 / (1.0 + np.exp(np.clip(s[:, None] - s[None, :], -30, 30)))
        lam = np.where(better, rho * delta, 0.0)
        weight = np.where(better, rho * (1.0 - rho) * delta, 0.0)
        gradient[rows] = -lam.sum(axis=1) + lam.sum(axis=0)
        hessian[rows] = weight.sum(axis=1) + weight.sum(axis=0)
    return gradient, np.maximum(hessian, 1e-6)


def ndcg_at(raw: np.ndarray, relevance: np.ndarray, index: list[np.ndarray], k: int = 10) -> float:
    """Средний NDCG@k по спискам: 1 — верхушка списка идеальна."""
    values: list[float] = []
    for rows in index:
        gains = 2.0**relevance[rows] - 1.0
        idcg = _ideal_dcg(gains, k)
        if idcg <= 0:
            continue
        top = np.argsort(-raw[rows], kind="stable")[:k]
        dcg = float((gains[top] / np.log2(np.arange(2, len(top) + 2))).sum())
        values.append(dcg / idcg)
    return float(np.mean(values)) if values else 0.0


def _bin_edges(x: np.ndarray) -> list[np.ndarray]:
    """Границы корзин по квантилям: редкие значения не съедают разрешение."""
    edges: list[np.ndarray] = []
    for column in range(x.shape[1]):
        values = np.unique(x[:, column])
        if len(values) <= MAX_BINS:
            edges.append(values[:-1] if len(values) > 1 else np.array([values[0]]))
        else:
            quantiles = np.quantile(x[:, column], np.linspace(0, 1, MAX_BINS + 1)[1:-1])
            edges.append(np.unique(quantiles))
    return edges


def _grow(
    binned: np.ndarray,
    edges: list[np.ndarray],
    gradient: np.ndarray,
    hessian: np.ndarray,
    rows: np.ndarray,
    depth: int,
    max_depth: int,
    min_samples_leaf: int,
    lambda_: float,
    min_gain: float,
    tree: TreeArrays,
    importance: np.ndarray,
) -> int:
    node = tree.add_node()
    g_sum = float(gradient[rows].sum())
    h_sum = float(hessian[rows].sum())
    tree.value[node] = -g_sum / (h_sum + lambda_)

    if depth >= max_depth or len(rows) < 2 * min_samples_leaf:
        return node

    parent_score = (g_sum * g_sum) / (h_sum + lambda_)
    best = (min_gain, -1, -1)  # выигрыш, признак, номер корзины
    for column in range(binned.shape[1]):
        bins = binned[rows, column]
        count = len(edges[column]) + 1
        g_hist = np.bincount(bins, weights=gradient[rows], minlength=count)
        h_hist = np.bincount(bins, weights=hessian[rows], minlength=count)
        n_hist = np.bincount(bins, minlength=count)
        g_left = np.cumsum(g_hist)[:-1]
        h_left = np.cumsum(h_hist)[:-1]
        n_left = np.cumsum(n_hist)[:-1]
        g_right = g_sum - g_left
        h_right = h_sum - h_left
        n_right = len(rows) - n_left
        allowed = (n_left >= min_samples_leaf) & (n_right >= min_samples_leaf)
        if not allowed.any():
            continue
        gain = (
            (g_left * g_left) / (h_left + lambda_)
            + (g_right * g_right) / (h_right + lambda_)
            - parent_score
        ) / 2.0
        gain = np.where(allowed, gain, -np.inf)
        index = int(np.argmax(gain))
        if gain[index] > best[0]:
            best = (float(gain[index]), column, index)

    _, column, bin_index = best
    if column < 0:
        return node

    threshold = float(edges[column][bin_index])
    mask = binned[rows, column] <= bin_index
    left_rows, right_rows = rows[mask], rows[~mask]
    if len(left_rows) == 0 or len(right_rows) == 0:
        return node

    importance[column] += best[0]
    tree.feature[node] = column
    tree.threshold[node] = threshold
    tree.left[node] = _grow(
        binned, edges, gradient, hessian, left_rows, depth + 1, max_depth, min_samples_leaf, lambda_, min_gain, tree, importance
    )
    tree.right[node] = _grow(
        binned, edges, gradient, hessian, right_rows, depth + 1, max_depth, min_samples_leaf, lambda_, min_gain, tree, importance
    )
    return node


def train(
    x: np.ndarray,
    y: np.ndarray,
    *,
    trees: int = 300,
    learning_rate: float = 0.08,
    max_depth: int = 5,
    min_samples_leaf: int = 20,
    lambda_: float = 1.0,
    min_gain: float = 1e-4,
    subsample: float = 0.9,
    seed: int = 0,
    validation: tuple[np.ndarray, np.ndarray] | None = None,
    patience: int = 30,
    loss: str = "logistic",
    groups: np.ndarray | None = None,
    validation_groups: np.ndarray | None = None,
) -> Booster:
    """Обучение с ранней остановкой по отложенной выборке.

    `loss="logistic"` — классификация «позиция пройдёт проверку», `loss="squared"` — регрессия
    на величину, которую хотим предсказать. Различаются только начальное приближение, градиент,
    гессиан и то, по чему считается ошибка на отложенной выборке.
    """
    if loss not in ("logistic", "squared", "lambdarank"):
        raise ValueError(f"Неизвестная функция потерь: {loss}")
    if loss == "lambdarank" and groups is None:
        raise ValueError("Для обучения порядку нужны группы: какие строки — один список кандидатов")
    index = _group_index(groups) if loss == "lambdarank" else []
    validation_index = (
        _group_index(validation_groups) if loss == "lambdarank" and validation_groups is not None else []
    )
    rng = np.random.default_rng(seed)
    edges = _bin_edges(x)
    binned = np.empty(x.shape, dtype=np.int32)
    for column in range(x.shape[1]):
        binned[:, column] = np.searchsorted(edges[column], x[:, column], side="left")

    if loss == "squared":
        init = float(y.mean())
    elif loss == "lambdarank":
        init = 0.0
    else:
        share = float(np.clip(y.mean(), 1e-6, 1 - 1e-6))
        init = float(np.log(share / (1 - share)))
    raw = np.full(len(y), init)
    importance = np.zeros(x.shape[1])
    grown: list[TreeArrays] = []

    best_loss, best_count, since_best = np.inf, 0, 0
    validation_raw = np.full(len(validation[1]), init) if validation is not None else None

    for _ in range(trees):
        if loss == "squared":
            gradient = raw - y
            hessian = np.ones(len(y))
        elif loss == "lambdarank":
            gradient, hessian = lambda_gradients(raw, y, index)
        else:
            probability = 1.0 / (1.0 + np.exp(-np.clip(raw, -30, 30)))
            gradient = probability - y
            hessian = np.maximum(probability * (1 - probability), 1e-6)
        rows = np.arange(len(y))
        if subsample < 1.0:
            rows = rng.choice(rows, size=int(len(y) * subsample), replace=False)
        tree = TreeArrays()
        _grow(binned, edges, gradient, hessian, rows, 0, max_depth, min_samples_leaf, lambda_, min_gain, tree, importance)
        grown.append(tree)
        raw += learning_rate * tree.predict(x)

        if validation is not None and validation_raw is not None:
            validation_raw += learning_rate * tree.predict(validation[0])
            if loss == "squared":
                current = float(np.mean((validation_raw - validation[1]) ** 2))
            elif loss == "lambdarank":
                current = -ndcg_at(validation_raw, validation[1], validation_index)
            else:
                p = 1.0 / (1.0 + np.exp(-np.clip(validation_raw, -30, 30)))
                current = float(
                    -np.mean(validation[1] * np.log(p + 1e-9) + (1 - validation[1]) * np.log(1 - p + 1e-9))
                )
            if current < best_loss - 1e-5:
                best_loss, best_count, since_best = current, len(grown), 0
            else:
                since_best += 1
                if since_best >= patience:
                    break

    if validation is not None and best_count:
        grown = grown[:best_count]
    total = importance.sum() or 1.0
    return Booster(
        trees=grown, init=init, learning_rate=learning_rate, importance=importance / total, loss=loss
    )
