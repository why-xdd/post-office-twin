"""Очередь в пик на плане: сценарий нагрузки → минуты ожидания → люди на чертеже.

WFM считает людей и смены, модель очереди — минуты. Здесь то же самое переводится в геометрию:
сколько человек стоит в очереди в загруженный момент пика, где именно они стоят на плане,
помещаются ли в накопитель или выходят в тамбур, и насколько при этом сужается проход от входа
к окнам.

Сценарий — то, что меняет начальник отделения или проектировщик:
- рост потока (сезонный пик, распродажа маркетплейса);
- доля стандартных операций, ушедших в зону самообслуживания (пилот «до 16:00»);
- окна, которые закрыты (нет сотрудника) или неисправны (обращение о поломке).

Все числа, которых нет в плане, — явные допущения и попадают в ответ.
"""

from __future__ import annotations

import math

from pydantic import BaseModel, Field
from shapely.affinity import rotate
from shapely.geometry import LineString, MultiPolygon, Point, Polygon
from shapely.ops import unary_union

from app.assistant import capacity
from app.geometry import core, passability
from app.models import Plan
from app.rules import evacuation

# Шаг между людьми в очереди и между рядами «змейки». Нормы на это нет: 0,6 м — плечом к спине
# с небольшим зазором, 0,8 м между рядами — проход ограждения. Это допущения, они выводятся.
STEP_M = 0.6
LANE_M = 0.8
# Человек как препятствие для прохода: круг диаметром 0,45 м (ширина плеч).
PERSON_RADIUS_M = 0.225
# Обслуживание в зоне самообслуживания: приём посылки за 1–2 минуты.
SELF_SERVICE_MIN = 1.5
SELF_SERVICE_SOURCE = "зоны самообслуживания Почты России: приём посылки за 1–2 минуты (ComNews, 9.04.2024)"
# Очередь, которая не рассасывается, рисуется такой, какой станет через час после начала пика.
OVERLOAD_HORIZON_H = 1.0
# Больше людей на плане не рисуем: при такой очереди картина и так ясна, а расчёт тяжелеет.
MAX_DRAWN = 150
SELF_SERVICE_TYPES = ("self_service_kiosk",)


class PeakScenario(BaseModel):
    flow_factor: float = Field(default=1.0, ge=0.3, le=4.0, description="Во сколько раз поток выше обычного пика")
    self_service_share: float = Field(
        default=0.0,
        ge=0.0,
        le=0.95,
        description="Доля операций у окон, ушедших в зону самообслуживания (пилот «до 16:00»)",
    )
    closed: list[str] = Field(default_factory=list, description="Окна без сотрудника")
    out_of_service: list[str] = Field(default_factory=list, description="Неисправные окна")


class QueuePerson(BaseModel):
    x: float
    y: float
    group: str
    # Стоит вне накопителя очереди: в зале, в тамбуре или на проходе.
    outside: bool


class PeakGroup(BaseModel):
    name: str
    windows: list[str]
    working: list[str]
    arrivals_per_hour: float
    service_min: float
    mean_wait_min: float | None = Field(description="None — очередь растёт без предела")
    meets_target: bool
    people: int = Field(description="Человек в очереди в загруженный момент пика")
    windows_needed: int
    queue_length_m: float


class PeakReport(BaseModel):
    available: bool
    reason: str | None = None
    scenario: PeakScenario
    target_wait_min: float
    peak_arrivals_per_hour: float | None = None
    groups: list[PeakGroup] = Field(default_factory=list)
    people: list[QueuePerson] = Field(default_factory=list)
    accumulator_slots: int | None = None
    outside: int = 0
    spills_into: list[str] = Field(default_factory=list, description="Зоны, куда выходит очередь")
    beyond_entrance: int = Field(default=0, description="Сколько людей не помещается в помещении")
    passage_without_queue_m: float | None = None
    passage_with_queue_m: float | None = None
    evacuation_required_m: float | None = None
    evacuation_with_queue_m: float | None = Field(
        default=None, description="Самый узкий путь эвакуации с людьми очереди, если очередь его сузила"
    )
    evacuation_zone: str | None = None
    advice: list[str] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)


def _min(value: float | None) -> str:
    return "без предела" if value is None else f"{value:.1f}".replace(".", ",") + " мин"


def _label(plan: Plan, item_id: str) -> str:
    item = next((e for e in plan.equipment if e.id == item_id), None)
    if item is None:
        return item_id
    return item.label or item.type


def _group_load(
    arrivals: float, service: float, servers: int, target: float
) -> tuple[float | None, int, bool]:
    """Ожидание, человек в очереди в загруженный момент и выполнена ли цель."""
    result = capacity._queue(servers, arrivals, service) if servers else {"stable": False}
    if result.get("stable"):
        wait = float(result["mean_wait_min"])
        return wait, min(MAX_DRAWN, int(result.get("queue_p95") or 0)), wait <= target
    # Перегрузка: очередь растёт. Рисуем её такой, какой она станет за час пика: уже полная
    # очередь почти загруженных окон плюс те, кого окна не успевают принять.
    capacity_per_hour = servers * 60.0 / service
    excess = arrivals - capacity_per_hour
    full = 0
    if servers:
        nearly = capacity._queue(servers, capacity_per_hour * 0.95, service)
        full = int(nearly.get("queue_p95") or 0)
    return None, min(MAX_DRAWN, max(1, full + math.ceil(excess * OVERLOAD_HORIZON_H))), False


def analyze(plan: Plan, scenario: PeakScenario | None = None) -> PeakReport:
    scenario = scenario or PeakScenario()
    target = plan.ops.target_wait_min or capacity.MAX_ACCEPTABLE_WAIT_MIN
    hints = plan.flow_hints
    if hints is None or not hints.avg_daily_visitors:
        return PeakReport(
            available=False,
            reason="В плане нет потока посетителей: заполните «Посетителей в день» в свойствах плана.",
            scenario=scenario,
            target_wait_min=target,
        )
    hours = plan.ops.working_hours or capacity.DEFAULT_WORKING_HOURS
    peak_factor = plan.ops.peak_to_average or capacity.PEAK_TO_AVERAGE
    assumptions: list[str] = []
    peak = hints.avg_daily_visitors / hours * peak_factor * scenario.flow_factor
    assumptions.append(
        f"Поток в час пик {peak:.0f} чел/ч: {hints.avg_daily_visitors} посетителей за {hours:g} ч, "
        f"пик в {peak_factor:g} раза выше среднего"
        + (f", сценарий ×{scenario.flow_factor:g}" if scenario.flow_factor != 1 else "")
        + "."
    )
    assumptions.append(
        f"Цель — среднее ожидание не более {target:g} мин"
        + (" (задана в паспорте отделения)." if plan.ops.target_wait_min else f": {capacity.TARGET_WAIT_SOURCE}.")
    )
    shares = capacity._path_shares(plan)
    if not shares:
        assumptions.append("Доли маршрутов не заданы — весь поток направлен к окнам обслуживания.")
    unavailable = set(scenario.closed) | set(scenario.out_of_service)

    loads: list[tuple[str, list[str], list[str], float, float]] = []
    moved = 0.0
    for name, types in capacity.GROUPS.items():
        windows = core.equipment_by_type(plan, *types)
        share = sum(shares.get(t, 0.0) for t in types) if shares else (1.0 if types == ("cashier_window",) else 0.0)
        if not windows and share == 0:
            continue
        arrivals = peak * share
        if types == ("cashier_window",) and scenario.self_service_share > 0:
            moved = arrivals * scenario.self_service_share
            arrivals -= moved
        service = capacity._service_minutes(plan, types, assumptions)
        ids = [w.id for w in windows]
        loads.append((name, ids, [i for i in ids if i not in unavailable], arrivals, service))
    if moved > 0:
        kiosks = core.equipment_by_type(plan, *SELF_SERVICE_TYPES)
        ids = [k.id for k in kiosks]
        loads.append(("Самообслуживание", ids, [i for i in ids if i not in unavailable], moved, SELF_SERVICE_MIN))
        assumptions.append(
            f"В зону самообслуживания уходит {scenario.self_service_share:.0%} операций у окон; "
            f"обслуживание {SELF_SERVICE_MIN:g} мин — {SELF_SERVICE_SOURCE}."
        )
        if not kiosks:
            assumptions.append("Терминалов самообслуживания на плане нет — ушедшему потоку негде обслужиться.")
    if scenario.out_of_service:
        assumptions.append(
            "Неисправны: " + ", ".join(_label(plan, i) for i in scenario.out_of_service) + "."
        )
    if scenario.closed:
        assumptions.append("Закрыты (нет сотрудника): " + ", ".join(_label(plan, i) for i in scenario.closed) + ".")

    groups: list[PeakGroup] = []
    for name, ids, working, arrivals, service in loads:
        wait, people, meets = _group_load(arrivals, service, len(working), target)
        groups.append(
            PeakGroup(
                name=name,
                windows=ids,
                working=working,
                arrivals_per_hour=round(arrivals, 1),
                service_min=round(service, 2),
                mean_wait_min=None if wait is None else round(wait, 1),
                meets_target=meets,
                people=people,
                windows_needed=capacity.windows_needed(arrivals, service, target) if arrivals > 0 else 0,
                queue_length_m=0.0,
            )
        )

    report = PeakReport(
        available=True,
        scenario=scenario,
        target_wait_min=target,
        peak_arrivals_per_hour=round(peak, 1),
        groups=groups,
        assumptions=assumptions,
    )
    _place_people(plan, report)
    _passage(plan, report)
    report.advice = _advice(plan, report, scenario)
    report.assumptions = list(dict.fromkeys(report.assumptions))
    report.assumptions.append(
        f"Очередь показана в загруженный момент пика (95% времени она короче); шаг между людьми "
        f"{STEP_M:g} м, между рядами накопителя {LANE_M:g} м — допущения."
    )
    if any(g.mean_wait_min is None for g in groups):
        report.assumptions.append(
            "Где окна не справляются, очередь растёт без предела; на плане она показана такой, "
            f"какой станет через {OVERLOAD_HORIZON_H:g} ч пика."
        )
    return report


# ---------- люди на плане ----------


def _standing_space(plan: Plan) -> Polygon | MultiPolygon | None:
    """Где может стоять человек: свободный пол с отступом плеч от оборудования и стен."""
    space = passability.free_space(plan)
    if space is None or space.is_empty:
        return None
    return space.buffer(-PERSON_RADIUS_M)


def _front(plan: Plan, working: list[str], all_ids: list[str]) -> tuple[float, float] | None:
    """Голова очереди: середина точек, где посетители стоят у окон группы."""
    ids = working or all_ids
    items = [e for e in plan.equipment if e.id in ids]
    if not items:
        return None
    points = [passability.client_access_point(plan, item) for item in items]
    return (sum(p[0] for p in points) / len(points), sum(p[1] for p in points) / len(points))


def _lane_slots(area: Polygon | MultiPolygon, front: tuple[float, float]) -> list[tuple[float, float]]:
    """Места «змейкой» в накопителе: ряды вдоль длинной стороны, первым — ближайший к окнам."""
    if area.is_empty:
        return []
    box = area.minimum_rotated_rectangle
    corners = list(box.exterior.coords)[:4]
    edges = [(corners[i], corners[(i + 1) % 4]) for i in range(4)]
    long_edge = max(edges, key=lambda e: math.dist(e[0], e[1]))
    angle = math.degrees(math.atan2(long_edge[1][1] - long_edge[0][1], long_edge[1][0] - long_edge[0][0]))
    origin = area.centroid
    flat = rotate(area, -angle, origin=origin)
    flat_front = rotate(Point(front), -angle, origin=origin)
    min_x, min_y, max_x, max_y = flat.bounds
    lanes: list[list[tuple[float, float]]] = []
    y = min_y + LANE_M / 2
    while y <= max_y - LANE_M / 4:
        row = []
        x = min_x + STEP_M / 2
        while x <= max_x:
            if flat.contains(Point(x, y)):
                row.append((x, y))
            x += STEP_M
        if row:
            lanes.append(row)
        y += LANE_M
    # Первый ряд — ближайший к голове очереди; внутри ряда направление чередуется.
    lanes.sort(key=lambda row: abs(row[0][1] - flat_front.y))
    ordered: list[tuple[float, float]] = []
    forward = flat_front.x <= (min_x + max_x) / 2
    for row in lanes:
        ordered.extend(row if forward else list(reversed(row)))
        forward = not forward
    back = []
    for x, y in ordered:
        p = rotate(Point(x, y), angle, origin=origin)
        back.append((round(p.x, 3), round(p.y, 3)))
    return back


def _walk(
    start: tuple[float, float],
    goal: tuple[float, float],
    space: Polygon | MultiPolygon,
    taken: list[tuple[float, float]],
    count: int,
) -> tuple[list[tuple[float, float]], int]:
    """Хвост очереди по прямой к выходу. Возвращает места и сколько людей не поместилось."""
    placed: list[tuple[float, float]] = []
    length = math.dist(start, goal)
    if length < 1e-6:
        return placed, count
    ux, uy = (goal[0] - start[0]) / length, (goal[1] - start[1]) / length
    distance = STEP_M
    while len(placed) < count and distance <= length:
        point = (start[0] + ux * distance, start[1] + uy * distance)
        distance += STEP_M / 2
        if not space.contains(Point(point)):
            continue
        if any(math.dist(point, other) < STEP_M * 0.9 for other in taken + placed):
            continue
        placed.append((round(point[0], 3), round(point[1], 3)))
    return placed, count - len(placed)


def _grow(
    start: tuple[float, float],
    heading: tuple[float, float],
    space: Polygon | MultiPolygon,
    taken: list[tuple[float, float]],
    count: int,
    hall: tuple[float, float],
) -> tuple[list[tuple[float, float]], int]:
    """Очередь от лицевой стороны стойки в зал: шаг за шагом, огибая препятствия.

    На каждом шаге берётся направление, ближайшее к прежнему, из тех, где человек помещается;
    при равных — то, что ведёт к середине зала. Так очередь не упирается в стену и не уходит
    в тамбур, если в зале есть место.
    """
    placed: list[tuple[float, float]] = []
    here = start
    hx, hy = heading
    turns = (0, 25, -25, 50, -50, 80, -80, 115, -115)
    while len(placed) < count:
        options = []
        for turn in turns:
            angle = math.radians(turn)
            dx = hx * math.cos(angle) - hy * math.sin(angle)
            dy = hx * math.sin(angle) + hy * math.cos(angle)
            point = (here[0] + dx * STEP_M, here[1] + dy * STEP_M)
            if not space.contains(Point(point)):
                continue
            if any(math.dist(point, other) < STEP_M * 0.9 for other in taken + placed):
                continue
            towards_hall = math.dist(point, hall)
            options.append((abs(turn), towards_hall, point, (dx, dy)))
        if not options:
            break
        options.sort(key=lambda o: (o[0] // 50, o[1]))
        _, _, point, (hx, hy) = options[0]
        point = (round(point[0], 3), round(point[1], 3))
        placed.append(point)
        here = point
    return placed, count - len(placed)


def _heading(plan: Plan, ids: list[str], front: tuple[float, float]) -> tuple[float, float]:
    """Направление очереди: от стойки к посетителю, то есть прочь от её лицевой стороны."""
    items = [e for e in plan.equipment if e.id in ids]
    if not items:
        return (0.0, -1.0)
    cx = sum(e.position[0] for e in items) / len(items)
    cy = sum(e.position[1] for e in items) / len(items)
    dx, dy = front[0] - cx, front[1] - cy
    length = math.hypot(dx, dy) or 1.0
    return (dx / length, dy / length)


def _place_people(plan: Plan, report: PeakReport) -> None:
    space = _standing_space(plan)
    entrance = passability.entrance_point(plan)
    if space is None or entrance is None:
        return
    accumulators = core.zone_by_type(plan, "queue_accumulator")
    taken: list[tuple[float, float]] = []
    used_accumulator = False
    for group in report.groups:
        if group.people <= 0:
            continue
        front = _front(plan, group.working, group.windows)
        if front is None:
            continue
        positions: list[tuple[float, float]] = []
        inside_count = 0
        # Накопитель — для окон обслуживания: туда очередь и рассчитана.
        if accumulators and group.name == "Окна обслуживания" and not used_accumulator:
            zone = min(accumulators, key=lambda z: core.zone_polygon(z).distance(Point(front)))
            area = core.zone_polygon(zone).intersection(space)
            slots = [s for s in _lane_slots(area, front) if all(math.dist(s, t) >= STEP_M * 0.9 for t in taken)]
            report.accumulator_slots = len(slots)
            positions = slots[: group.people]
            inside_count = len(positions)
            used_accumulator = True
        rest = group.people - len(positions)
        if rest > 0:
            if positions:
                # Накопитель полон: хвост тянется от его конца к выходу, как в жизни.
                tail, missing = _walk(positions[-1], entrance, space, taken + positions, rest)
            else:
                # Очередь к стойке или терминалу растёт от их лицевой стороны в зал.
                hall_centre = space.centroid
                tail, missing = _grow(
                    front,
                    _heading(plan, group.working or group.windows, front),
                    space,
                    taken,
                    rest,
                    (hall_centre.x, hall_centre.y),
                )
            positions.extend(tail)
            report.beyond_entrance += missing
        accumulator_shape = (
            unary_union([core.zone_polygon(z) for z in accumulators]) if accumulators else None
        )
        uses_accumulator = inside_count > 0 or (accumulators and group.name == "Окна обслуживания")
        for index, (x, y) in enumerate(positions):
            in_accumulator = accumulator_shape is not None and accumulator_shape.contains(Point(x, y))
            # Очередь к окнам рассчитана на накопитель: всё, что за ним, — перелив. Очередь к
            # стойке выдачи стоит у стойки, это норма; плохо, только если она ушла в тамбур.
            if uses_accumulator:
                outside = index >= inside_count and not in_accumulator
            else:
                zone_id = core.zone_of_point(plan, (x, y))
                zone = next((z for z in plan.zones if z.id == zone_id), None)
                outside = zone is not None and zone.type == "vestibule"
            report.people.append(QueuePerson(x=x, y=y, group=group.name, outside=outside))
        taken.extend(positions)
        chain = [front, *positions]
        group.queue_length_m = round(LineString(chain).length, 1) if len(chain) > 1 else 0.0
    report.outside = sum(1 for p in report.people if p.outside)
    zones: list[str] = []
    for person in report.people:
        if not person.outside:
            continue
        zone_id = core.zone_of_point(plan, (person.x, person.y))
        zone = next((z for z in plan.zones if z.id == zone_id), None)
        label = (zone.label or zone.type) if zone is not None else None
        if label and label not in zones:
            zones.append(label)
    report.spills_into = zones


def _passage(plan: Plan, report: PeakReport) -> None:
    """Насколько очередь сужает проход от входа к окнам: люди — препятствия на полу."""
    entrance = passability.entrance_point(plan)
    space = passability.free_space(plan)
    if entrance is None or space is None or space.is_empty:
        return
    goals = []
    for group in report.groups:
        front = _front(plan, group.working, group.windows)
        if front is not None:
            goals.append(front)
    if not goals:
        return
    base = passability.ErosionCache(space)
    report.passage_without_queue_m = min(base.width(Point(entrance), Point(g))[0] for g in goals)
    if not report.people:
        report.passage_with_queue_m = report.passage_without_queue_m
        return
    widths = []
    for goal in goals:
        # Человек у самого окна и есть цель пути — его не считаем препятствием.
        bodies = [
            Point(p.x, p.y).buffer(PERSON_RADIUS_M, quad_segs=6)
            for p in report.people
            if math.dist((p.x, p.y), goal) > STEP_M * 1.2
        ]
        crowded = space.difference(unary_union(bodies)) if bodies else space
        widths.append(passability.ErosionCache(crowded).width(Point(entrance), Point(goal))[0])
    report.passage_with_queue_m = min(widths)
    # Эвакуация: все люди очереди — препятствия. Сужение, которое есть и без очереди, — это
    # нарушение плана, его показывает проверка; здесь — только то, что добавляет пик.
    bodies = [Point(p.x, p.y).buffer(PERSON_RADIUS_M, quad_segs=6) for p in report.people]
    crowded = space.difference(unary_union(bodies))
    before: dict[str, float] = {}
    for neck in evacuation.bottlenecks(plan):
        before[neck.zone_id] = min(before.get(neck.zone_id, neck.measured_m), neck.measured_m)
    worst = None
    for neck in evacuation.bottlenecks(plan, crowded):
        if neck.zone_id in before and neck.measured_m >= before[neck.zone_id] - 0.05:
            continue
        if worst is None or neck.measured_m < worst.measured_m:
            worst = neck
    if worst is not None:
        report.evacuation_required_m = worst.required_m
        report.evacuation_with_queue_m = worst.measured_m
        report.evacuation_zone = worst.zone_label


# ---------- советы ----------


def _advice(plan: Plan, report: PeakReport, scenario: PeakScenario) -> list[str]:
    advice: list[str] = []
    target = report.target_wait_min
    for group in report.groups:
        if group.meets_target or group.arrivals_per_hour <= 0:
            continue
        idle_closed = [i for i in group.windows if i in scenario.closed]
        broken = [i for i in group.windows if i in scenario.out_of_service]
        if idle_closed:
            # Сколько закрытых окон открыть, чтобы уложиться в цель.
            for extra in range(1, len(idle_closed) + 1):
                wait, _, meets = _group_load(
                    group.arrivals_per_hour, group.service_min, len(group.working) + extra, target
                )
                if meets:
                    names = ", ".join(_label(plan, i) for i in idle_closed[:extra])
                    advice.append(
                        f"{group.name}: откройте {names} — ожидание станет около {_min(wait)} при цели {target:g} мин."
                    )
                    break
            else:
                advice.append(f"{group.name}: даже со всеми окнами цель {target:g} мин не держится.")
        if broken:
            wait, _, meets = _group_load(
                group.arrivals_per_hour, group.service_min, len(group.working) + len(broken), target
            )
            after = _min(wait)
            advice.append(
                f"{group.name}: неисправно {', '.join(_label(plan, i) for i in broken)} — после ремонта "
                f"ожидание {after}."
            )
        if group.windows_needed > len(group.windows):
            lacking = group.windows_needed - len(group.windows)
            advice.append(
                f"{group.name}: физически не хватает {lacking} {'окна' if lacking == 1 else 'окон'} — "
                f"место подберёт ассистент в «Редакторе»."
            )
    if report.outside:
        where = f": {', '.join(report.spills_into)}" if report.spills_into else ""
        advice.append(f"Очередь выходит за отведённое место — {report.outside} чел. стоят там, где не должны{where}.")
    if report.beyond_entrance:
        advice.append(f"{report.beyond_entrance} чел. не помещаются в помещении — очередь выходит на улицу.")
    width = report.passage_with_queue_m
    if (
        width is not None
        and report.passage_without_queue_m is not None
        and width < min(report.passage_without_queue_m, 1.2) - 0.01
    ):
        words = f"{width:.2f}".replace(".", ",")
        if width < 0.05:
            advice.append("Очередь перекрывает проход от входа к окнам: пройти к окнам можно только сквозь неё.")
        else:
            advice.append(
                f"Очередь сужает проход от входа к окнам до {words} м — "
                + (
                    "посетитель на кресле-коляске не проедет (нужно не меньше 0,9 м)."
                    if width < 0.9
                    else "меньше 1,2 м: с коляской не разъехаться."
                )
            )
    if report.evacuation_with_queue_m is not None and report.evacuation_required_m is not None:
        width = report.evacuation_with_queue_m
        words = "перекрывает его" if width < 0.05 else f"сужает его до {width:.2f} м".replace(".", ",")
        advice.append(
            f"Очередь стоит на пути эвакуации из зоны «{report.evacuation_zone}» и {words} при норме "
            f"{report.evacuation_required_m:g} м (СП 1.13130.2020, п. 4.3.3).".replace(".0 м", " м")
        )
    if not advice:
        advice.append(f"Все группы окон держат цель {target:g} мин, очередь помещается в накопитель.")
    return advice
