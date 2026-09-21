"""Пропускная способность отделения в час пик.

Модель — многоканальная очередь M/M/c (формула Эрланга C): посетители приходят случайно,
время обслуживания случайно, c окон обслуживают общую очередь. Для каждой группы окон
(кассы, выдача посылок) очередь считается отдельно: поток делится по долям маршрутов из
`flow_hints.typical_paths`.

Каждое значение, которого нет в данных плана, вводится как явное допущение и попадает в отчёт.
"""

from __future__ import annotations

import math

from pydantic import BaseModel, Field
from shapely.geometry import Point

from app.geometry import core
from app.models import Plan

# Допущения по умолчанию. Все они выводятся в отчёте, если применились.
DEFAULT_WORKING_HOURS = 12.0
PEAK_TO_AVERAGE = 1.8
DEFAULT_SERVICE_MIN = 2.5
DEFAULT_SERVICE_SOURCE = (
    "карточка оператора АРМ OPS115534-W01 в демо-плане заказчика «ОПС_115534_демо_плана»: "
    "«Ср. время обслуживания 2,5 мин»"
)
MAX_ACCEPTABLE_WAIT_MIN = 5.0
SENSITIVITY_MINUTES = (1.0, 1.5, 2.0, 2.5, 3.0, 3.5, 4.0, 5.0, 6.0, 7.0, 8.0)
STANDING_SQM_PER_PERSON = 0.5
QUEUE_QUANTILE = 0.95

GROUPS: dict[str, tuple[str, ...]] = {
    "Окна обслуживания": ("cashier_window",),
    "Выдача посылок": ("pvz_counter",),
}


class QueueGroup(BaseModel):
    name: str
    windows: list[str]
    arrivals_per_hour: float
    service_min: float
    utilization: float = Field(description="Загрузка окон; 1 и выше — очередь растёт без предела")
    stable: bool
    wait_probability: float | None = None
    mean_wait_min: float | None = None
    mean_queue: float | None = None
    queue_p95: int | None = Field(default=None, description="Длина очереди, не превышаемая в 95% времени")
    windows_needed: int = Field(description="Минимум окон, при котором среднее ожидание ≤ 5 мин")
    service_threshold_min: float | None = Field(
        default=None,
        description="Наибольшее среднее время обслуживания, при котором текущие окна держат "
        "ожидание в пределах 5 мин",
    )
    reserve_percent: float | None = Field(
        default=None, description="Запас по времени обслуживания относительно текущего, %"
    )
    sensitivity: list[dict[str, float | None]] = Field(
        default_factory=list, description="Среднее ожидание при разном времени обслуживания"
    )


class CapacityReport(BaseModel):
    available: bool
    reason: str | None = None
    daily_visitors: int | None = None
    peak_arrivals_per_hour: float | None = None
    groups: list[QueueGroup] = Field(default_factory=list)
    waiting_capacity_persons: int | None = None
    peak_queue_persons: int | None = None
    fits_waiting_area: bool | None = None
    assumptions: list[str] = Field(default_factory=list)


def erlang_c(servers: int, offered_load: float) -> float:
    """Вероятность того, что пришедший посетитель будет ждать."""
    if servers <= 0:
        return 1.0
    if offered_load >= servers:
        return 1.0
    term = 1.0
    total = 1.0
    for k in range(1, servers):
        term *= offered_load / k
        total += term
    top = term * offered_load / servers * servers / (servers - offered_load)
    return top / (total + top)


def _queue(servers: int, arrivals: float, service_min: float) -> dict[str, float | int | bool | None]:
    rate = 60.0 / service_min
    load = arrivals / rate
    utilization = load / servers if servers else math.inf
    if servers == 0 or utilization >= 1.0:
        return {"utilization": utilization, "stable": False}
    wait_prob = erlang_c(servers, load)
    mean_wait_h = wait_prob / (servers * rate - arrivals)
    mean_queue = arrivals * mean_wait_h
    if wait_prob <= 1 - QUEUE_QUANTILE:
        p95 = 0
    else:
        p95 = math.ceil(math.log((1 - QUEUE_QUANTILE) / wait_prob) / math.log(utilization))
    return {
        "utilization": utilization,
        "stable": True,
        "wait_probability": wait_prob,
        "mean_wait_min": mean_wait_h * 60.0,
        "mean_queue": mean_queue,
        "queue_p95": max(p95, 0),
    }


def service_threshold(servers: int, arrivals: float, max_wait_min: float = MAX_ACCEPTABLE_WAIT_MIN) -> float | None:
    """Наибольшее время обслуживания, при котором ожидание не превышает допустимого."""
    if servers == 0 or arrivals <= 0:
        return None
    low, high = 0.05, 120.0

    def ok(minutes: float) -> bool:
        result = _queue(servers, arrivals, minutes)
        return bool(result["stable"]) and float(result["mean_wait_min"] or 0) <= max_wait_min

    if not ok(low):
        return 0.0
    while high - low > 0.01:
        mid = (low + high) / 2.0
        if ok(mid):
            low = mid
        else:
            high = mid
    return round(low, 2)


def windows_needed(arrivals: float, service_min: float, max_wait_min: float = MAX_ACCEPTABLE_WAIT_MIN) -> int:
    servers = 1
    while servers < 60:
        result = _queue(servers, arrivals, service_min)
        if result["stable"] and float(result["mean_wait_min"] or 0) <= max_wait_min:
            return servers
        servers += 1
    return servers


def _path_shares(plan: Plan) -> dict[str, float]:
    """Доля потока на каждый тип оборудования по конечной точке типичных маршрутов."""
    shares: dict[str, float] = {}
    if plan.flow_hints is None:
        return shares
    for path in plan.flow_hints.typical_paths:
        end = Point(path.waypoints[-1])
        nearest = min(
            plan.equipment,
            key=lambda item: core.equipment_polygon(item).distance(end),
            default=None,
        )
        if nearest is not None:
            shares[nearest.type] = shares.get(nearest.type, 0.0) + path.frequency_share
    return shares


def _service_minutes(plan: Plan, types: tuple[str, ...], assumptions: list[str]) -> float:
    times = [
        item.staff.avg_service_time_min
        for item in core.equipment_by_type(plan, *types)
        if item.staff is not None and item.staff.avg_service_time_min
    ]
    if times:
        return sum(times) / len(times)
    if plan.ops.service_time_min:
        assumptions.append(
            f"Среднее время обслуживания {plan.ops.service_time_min:g} мин задано в паспорте отделения."
        )
        return plan.ops.service_time_min
    assumptions.append(
        f"Среднее время обслуживания не задано ни в карточках сотрудников, ни в паспорте — "
        f"принято {DEFAULT_SERVICE_MIN:g} мин; источник: {DEFAULT_SERVICE_SOURCE}."
    )
    return DEFAULT_SERVICE_MIN


def waiting_capacity(plan: Plan, assumptions: list[str]) -> int | None:
    zones = core.zone_by_type(plan, "waiting", "queue_accumulator")
    if not zones:
        return None
    counted: set[str] = set()
    seats = 0
    standing_area = 0.0
    for zone in zones:
        poly = core.zone_polygon(zone)
        occupied = 0.0
        for item in core.floor_equipment(plan):
            shape = core.equipment_polygon(item)
            if poly.intersects(shape):
                occupied += poly.intersection(shape).area
                # Скамья, заходящая в две зоны ожидания, учитывается один раз.
                if item.capacity_persons and item.id not in counted:
                    seats += item.capacity_persons
                    counted.add(item.id)
        if zone.type == "queue_accumulator":
            standing_area += max(poly.area - occupied, 0.0)
    assumptions.append(
        f"Стоящему в очереди посетителю отводится {STANDING_SQM_PER_PERSON:g} м² свободной "
        f"площади накопителя."
    )
    return seats + int(standing_area / STANDING_SQM_PER_PERSON)


def analyze(plan: Plan) -> CapacityReport:
    hints = plan.flow_hints
    if hints is None or not hints.avg_daily_visitors:
        return CapacityReport(
            available=False,
            reason="В плане нет данных о потоке посетителей (flow_hints.avg_daily_visitors).",
        )

    hours = plan.ops.working_hours or DEFAULT_WORKING_HOURS
    peak_factor = plan.ops.peak_to_average or PEAK_TO_AVERAGE
    assumptions: list[str] = [
        f"Отделение работает {hours:g} ч в день"
        + ("." if plan.ops.working_hours else " (допущение, задаётся в паспорте отделения)."),
        f"Поток в часы пик в {peak_factor:g} раза выше среднего за день"
        + ("." if plan.ops.peak_to_average else " (допущение, задаётся в паспорте отделения)."),
        "Посетители приходят независимо, время обслуживания случайно (модель M/M/c, формула "
        "Эрланга C). Допустимым считается среднее ожидание не более 5 мин.",
    ]
    average = hints.avg_daily_visitors / hours
    peak = average * peak_factor

    shares = _path_shares(plan)
    if not shares:
        assumptions.append("Доли маршрутов не заданы — весь поток направлен к окнам обслуживания.")

    groups: list[QueueGroup] = []
    for name, types in GROUPS.items():
        windows = core.equipment_by_type(plan, *types)
        share = sum(shares.get(t, 0.0) for t in types) if shares else (1.0 if types == ("cashier_window",) else 0.0)
        if not windows and share == 0:
            continue
        arrivals = peak * share
        service = _service_minutes(plan, types, assumptions)
        result = _queue(len(windows), arrivals, service)
        threshold = service_threshold(len(windows), arrivals)
        series = []
        for minutes in SENSITIVITY_MINUTES:
            point = _queue(len(windows), arrivals, minutes)
            series.append(
                {
                    "service_min": minutes,
                    "mean_wait_min": _round(point.get("mean_wait_min"), 1) if point["stable"] else None,
                }
            )
        groups.append(
            QueueGroup(
                name=name,
                windows=[w.id for w in windows],
                arrivals_per_hour=round(arrivals, 1),
                service_min=round(service, 2),
                utilization=round(float(result["utilization"]), 3)
                if math.isfinite(float(result["utilization"]))
                else 99.0,
                stable=bool(result["stable"]),
                wait_probability=_round(result.get("wait_probability"), 3),
                mean_wait_min=_round(result.get("mean_wait_min"), 1),
                mean_queue=_round(result.get("mean_queue"), 1),
                queue_p95=result.get("queue_p95"),  # type: ignore[arg-type]
                windows_needed=windows_needed(arrivals, service),
                service_threshold_min=threshold,
                reserve_percent=None
                if threshold is None
                else round((threshold - service) / service * 100.0, 0),
                sensitivity=series,
            )
        )

    capacity = waiting_capacity(plan, assumptions)
    peak_queue = None
    if groups and all(g.stable for g in groups):
        peak_queue = sum(g.queue_p95 or 0 for g in groups)

    return CapacityReport(
        available=True,
        daily_visitors=hints.avg_daily_visitors,
        peak_arrivals_per_hour=round(peak, 1),
        groups=groups,
        waiting_capacity_persons=capacity,
        peak_queue_persons=peak_queue,
        fits_waiting_area=None if capacity is None or peak_queue is None else peak_queue <= capacity,
        assumptions=list(dict.fromkeys(assumptions)),
    )


def _round(value: object, digits: int) -> float | None:
    return None if value is None else round(float(value), digits)  # type: ignore[arg-type]
