"""Память о посчитанном.

Проверка плана и подбор расстановки — чистые функции: для одного и того же документа ответ
всегда один. Значит, второй такой же запрос можно не считать заново. Это особенно важно на
бесплатном хостинге с одним слабым ядром: открытие плана, предпросмотр и повторное нажатие
кнопки перестают стоить секунды.

Ключ — отпечаток документа, а не его текст: порядок ключей в JSON не должен влиять на попадание.
Хранилище ограничено по числу записей и по возрасту, иначе память утечёт на длинной сессии.
"""

from __future__ import annotations

import hashlib
import json
import threading
import time
from collections import OrderedDict
from typing import Any, Callable, TypeVar

T = TypeVar("T")

MAX_ENTRIES = 64
TTL_S = 30 * 60


def fingerprint(*parts: Any) -> str:
    """Отпечаток аргументов: одинаковые данные дают одинаковый ключ независимо от порядка ключей."""
    payload = json.dumps(parts, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class ResultCache:
    """Небольшой кэш «последних использованных» с временем жизни записи.

    Доступ защищён мьютексом: обработчики запросов выполняются в пуле потоков, а словарь
    OrderedDict не рассчитан на одновременные изменения из нескольких потоков.
    """

    def __init__(self, max_entries: int = MAX_ENTRIES, ttl_s: float = TTL_S) -> None:
        self._entries: OrderedDict[str, tuple[float, Any]] = OrderedDict()
        self._lock = threading.Lock()
        self._max_entries = max_entries
        self._ttl_s = ttl_s
        self.hits = 0
        self.misses = 0

    def get_or_compute(self, key: str, compute: Callable[[], T]) -> T:
        with self._lock:
            entry = self._entries.get(key)
            if entry is not None and time.monotonic() - entry[0] <= self._ttl_s:
                self._entries.move_to_end(key)
                self.hits += 1
                return entry[1]
            if entry is not None:
                del self._entries[key]
        # Расчёт идёт вне мьютекса: он длинный, и держать на нём остальные потоки нельзя.
        self.misses += 1
        value = compute()
        with self._lock:
            self._entries[key] = (time.monotonic(), value)
            self._entries.move_to_end(key)
            while len(self._entries) > self._max_entries:
                self._entries.popitem(last=False)
        return value

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()

    def stats(self) -> dict[str, int]:
        with self._lock:
            return {"записей": len(self._entries), "попаданий": self.hits, "промахов": self.misses}


validation_cache = ResultCache()
assistant_cache = ResultCache(max_entries=16)
