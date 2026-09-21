"""Очередь на тяжёлые расчёты.

Подбор расстановки занимает секунды процессорного времени. На бесплатном хостинге ядро одно,
и два одновременных запроса не ускоряют работу, а растягивают оба и съедают память. Поэтому
тяжёлые запросы проходят по одному: следующий ждёт своей очереди, а если ожидание затянулось —
получает понятный ответ, что сервис занят, вместо обрыва соединения.
"""

from __future__ import annotations

import os
import threading
from contextlib import contextmanager
from typing import Iterator

from fastapi import HTTPException


def _setting(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, default))
    except ValueError:
        return default


MAX_PARALLEL = max(1, int(_setting("OPS_TWIN_MAX_PARALLEL", 1)))
WAIT_TIMEOUT_S = _setting("OPS_TWIN_WAIT_TIMEOUT", 45.0)

_slots = threading.BoundedSemaphore(MAX_PARALLEL)


@contextmanager
def heavy_slot(what: str = "расчёт") -> Iterator[None]:
    """Пропускает не больше MAX_PARALLEL тяжёлых расчётов одновременно."""
    if not _slots.acquire(timeout=WAIT_TIMEOUT_S):
        raise HTTPException(
            status_code=503,
            detail=(
                f"Сервис сейчас занят: идёт {what} для другого плана. "
                "Повторите запрос через несколько секунд."
            ),
            headers={"Retry-After": "10"},
        )
    try:
        yield
    finally:
        _slots.release()
