"""Синтетический журнал электронной очереди для демонстрационного плана.

Настоящих журналов отделений у нас нет, поэтому журнал моделируется: поток по часам — из
`flow_hints` демонстрационного плана (посетителей в день, часы пик, доли маршрутов), обслуживание
случайной длительности, общая очередь к окнам. Все окна открыты весь день — так работает
отделение, которому ещё не подбирали расписание.

Запуск из services/api:
    uv run python scripts/make_queue_sample.py
"""

from __future__ import annotations

import heapq
import json
import random
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
DEMO = ROOT / "data" / "demo_plan.json"
OUT = ROOT / "data" / "queue_log_sample.csv"
SEED = 20260922
DAYS = ("2026-09-14", "2026-09-15", "2026-09-16", "2026-09-17", "2026-09-18")
OPEN_HOUR, CLOSE_HOUR = 8, 20
PEAK_WEIGHT = 2.2  # во сколько раз час пик загруженнее обычного часа
SERVICES = {
    # группа: (окна, среднее обслуживание в минутах, названия услуг)
    "Окна обслуживания": (("Окно 1", "Окно 2", "Окно 3"), 2.6, ("Отправка письма", "Оплата услуг", "Подписка", "Денежный перевод")),
    "Выдача посылок": (("Выдача",), 2.5, ("Выдача посылки",)),
}


def hourly_weights(peaks: list[str]) -> dict[int, float]:
    peak_hours: set[int] = set()
    for span in peaks:
        start, end = (int(part.split(":")[0]) for part in span.split("-"))
        peak_hours.update(range(start, end))
    weights = {h: (PEAK_WEIGHT if h in peak_hours else 1.0) for h in range(OPEN_HOUR, CLOSE_HOUR)}
    total = sum(weights.values())
    return {h: w / total for h, w in weights.items()}


def simulate(rng: random.Random, day: str, arrivals: dict[int, float], windows: tuple[str, ...], service_min: float,
             names: tuple[str, ...]) -> list[tuple[datetime, datetime, datetime, str, str]]:
    start = datetime.fromisoformat(day)
    moments: list[datetime] = []
    for hour, rate in arrivals.items():
        t = start + timedelta(hours=hour)
        end = t + timedelta(hours=1)
        while True:
            t += timedelta(minutes=rng.expovariate(rate / 60.0))
            if t >= end:
                break
            moments.append(t)
    moments.sort()
    free = [(start + timedelta(hours=OPEN_HOUR), name) for name in windows]
    heapq.heapify(free)
    tickets = []
    for issued in moments:
        available, window = heapq.heappop(free)
        called = max(issued, available)
        finished = called + timedelta(minutes=rng.expovariate(1.0 / service_min))
        heapq.heappush(free, (finished, window))
        tickets.append((issued, called, finished, window, rng.choice(names)))
    return tickets


def main() -> None:
    plan = json.loads(DEMO.read_text(encoding="utf-8"))
    hints = plan["flow_hints"]
    weights = hourly_weights(hints.get("peak_hours_local", []))
    shares = {"Окна обслуживания": 0.0, "Выдача посылок": 0.0}
    for path in hints["typical_paths"]:
        label = path["label"].lower()
        if "окн" in label:
            shares["Окна обслуживания"] += path["frequency_share"]
        elif "выдач" in label:
            shares["Выдача посылок"] += path["frequency_share"]
    rng = random.Random(SEED)
    rows: list[tuple[datetime, datetime, datetime, str, str]] = []
    for day in DAYS:
        daily = hints["avg_daily_visitors"] * rng.uniform(0.9, 1.1)
        for group, (windows, service, names) in SERVICES.items():
            arrivals = {h: daily * shares[group] * w for h, w in weights.items()}
            rows.extend(simulate(rng, day, arrivals, windows, service, names))
    rows.sort()
    lines = ["Талон;Дата;Выдан;Вызван;Завершён;Окно;Услуга"]
    for number, (issued, called, finished, window, name) in enumerate(rows, 1):
        lines.append(
            f"{number};{issued:%d.%m.%Y};{issued:%H:%M:%S};{called:%H:%M:%S};{finished:%H:%M:%S};{window};{name}"
        )
    OUT.write_text("\r\n".join(lines) + "\r\n", encoding="utf-8")
    print(f"{OUT}: {len(rows)} талонов за {len(DAYS)} дней")


if __name__ == "__main__":
    main()
