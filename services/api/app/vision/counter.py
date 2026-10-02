"""Подсчёт посетителей по кадрам камеры зала и привязка их к плану отделения.

Камера в отделении уже есть: она висит в зале и пишет обычное видео. Здесь из этого видео
получается то, чего не даёт ни один журнал, — где посетители стоят на самом деле.

Как считается, по шагам, без нейросетей: каждый шаг можно объяснить проверяющему.

1. **Фон.** По выборке кадров берётся 75-й перцентиль яркости каждого пикселя. Стены и мебель
   стоят на месте и попадают в фон целиком; посетитель закрывает свой пиксель лишь часть записи,
   поэтому в фон не уходит. Перцентиль вместо медианы — ради очереди: она стоит подолгу.
2. **Движение.** Кадр минус фон, порог по яркости, затем чистка: пятна меньше человека
   отбрасываются, дырки внутри пятна закрываются.
3. **Пятна.** Оставшиеся области разбиваются на связные пятна (поиск в ширину по маске).
4. **Метры.** Один раз задаётся соответствие: четыре точки на кадре и тот же прямоугольник на
   плане. По ним строится гомография, и низ каждого пятна (точка опоры человека на полу)
   переводится в координаты плана.
5. **Зоны и время.** Точка на плане попадает в зону (ожидание, накопитель очереди, зал), а кадр —
   в свою секунду записи. Отсюда: сколько человек в каждой зоне в каждый момент, максимум и
   среднее, и тепловая карта — где стоят чаще всего.

Чего метод не умеет, и это выводится в допущениях: различать людей в плотной очереди. Слипшиеся
силуэты считаются по площади пятна, поэтому число в пике — оценка снизу.
"""

from __future__ import annotations

import io
import math
from collections import deque

import numpy as np
from PIL import Image
from pydantic import BaseModel, Field
from shapely.geometry import Point

from app.assistant.capacity import STANDING_SQM_PER_PERSON
from app.geometry import core
from app.models import Plan, Zone

# Кадр ужимается до этой ширины: мелкие детали не нужны, а считать быстрее в разы.
WORK_WIDTH = 320
# Сколько кадров берётся на фоновую модель: больше — устойчивее, дольше считать.
BACKGROUND_FRAMES = 41
# Каким перцентилем яркости берётся фон. 50 (медиана) прячет очередь, которая стоит на месте
# больше половины записи; 75 оставляет её видимой, а неподвижную мебель по-прежнему убирает.
BACKGROUND_PERCENTILE = 75
# Порог движения в единицах яркости (0–255) поверх шума самой камеры.
MOTION_THRESHOLD = 18.0
# Человек сверху занимает примерно столько площади пола: плечи около 0,45 × 0,45 м.
# Значение подбирается под камеру и передаётся в запросе; проверено на синтетической записи
# (см. scripts/make_camera_sample.py): при 0,2 м² расхождение с правдой симуляции 8 %.
PERSON_AREA_SQM = 0.2
# Пятно меньше этой доли человека — шум (блик, тень от двери, дрожание камеры).
MIN_BLOB_SHARE = 0.4
# Сторона ячейки тепловой карты. Мелкая ячейка нужна ради резкости: пятно скопления должно
# иметь узнаваемую форму, а не расплываться по полу.
HEAT_CELL_M = 0.1
# Радиус, которым человек «засвечивает» карту вокруг своей точки опоры: примерно место, которое
# он занимает вместе с личным пространством. Подобран на демо-записи из четырёх вариантов:
# меньше — карта рассыпается на отдельные точки, больше — ряд очереди сливается в кляксу. Каждое появление кладётся на карту
# этим пятном, а не одной ячейкой, — поэтому карта получается плавной без размытия.
HEAT_SPOT_M = 0.4


class VisionError(ValueError):
    """Данные камеры не годятся для расчёта: об этом нужно сказать человеку словами."""


def decode_frames(payload: list[bytes]) -> tuple[np.ndarray, tuple[int, int]]:
    """Кадры в один массив (кадр, высота, ширина) яркости 0–255 и размер исходного кадра."""
    if len(payload) < 8:
        raise VisionError("Нужно хотя бы 8 кадров: по одному-двум фон не построить.")
    frames: list[np.ndarray] = []
    source: tuple[int, int] | None = None
    for raw in payload:
        try:
            image = Image.open(io.BytesIO(raw))
            image.load()
        except Exception as error:  # noqa: BLE001 — любая битая картинка объясняется одинаково
            raise VisionError("Один из кадров не читается как изображение.") from error
        if source is None:
            source = image.size
        elif image.size != source:
            raise VisionError("Кадры разного размера: запись должна быть с одной камеры.")
        scale = WORK_WIDTH / image.width
        small = image.convert("L").resize((WORK_WIDTH, max(1, round(image.height * scale))))
        frames.append(np.asarray(small, dtype=np.float32))
    return np.stack(frames), source  # type: ignore[return-value]


def homography(source: list[tuple[float, float]], target: list[tuple[float, float]]) -> np.ndarray:
    """Матрица 3×3, переводящая точки кадра в точки плана, по четырём парам точек.

    Обычное преобразование перспективы: прямоугольник пола на кадре выглядит трапецией, и эта
    матрица возвращает ему прямые углы.
    """
    if len(source) != 4 or len(target) != 4:
        raise VisionError("Для привязки к плану нужно ровно четыре пары точек.")
    rows = []
    for (x, y), (u, v) in zip(source, target):
        rows.append([x, y, 1, 0, 0, 0, -u * x, -u * y])
        rows.append([0, 0, 0, x, y, 1, -v * x, -v * y])
    matrix = np.array(rows, dtype=np.float64)
    values = np.array([c for point in target for c in point], dtype=np.float64)
    try:
        solution = np.linalg.solve(matrix, values)
    except np.linalg.LinAlgError as error:
        raise VisionError(
            "Точки привязки лежат на одной линии или совпадают — отметьте четыре угла области."
        ) from error
    return np.append(solution, 1.0).reshape(3, 3)


def project(matrix: np.ndarray, points: np.ndarray) -> np.ndarray:
    """Точки кадра (N×2) в координаты плана (N×2)."""
    ones = np.ones((len(points), 1), dtype=np.float64)
    raw = np.hstack([points, ones]) @ matrix.T
    weights = np.where(np.abs(raw[:, 2:3]) < 1e-9, 1e-9, raw[:, 2:3])
    return raw[:, :2] / weights


def _box_mean(mask: np.ndarray, radius: int) -> np.ndarray:
    """Среднее по квадрату со стороной 2r+1 вокруг каждого пикселя (через интегральную сумму).

    Для маски движения это доля единиц — так чистятся мелкие пятна; для тепловой карты то же
    среднее сглаживает соседние ячейки.
    """
    padded = np.pad(mask.astype(np.float32), radius + 1, mode="edge")
    integral = padded.cumsum(axis=0).cumsum(axis=1)
    size = 2 * radius + 1
    bottom, right = integral.shape[0] - 1, integral.shape[1] - 1
    rows = np.arange(mask.shape[0]) + radius + 1
    cols = np.arange(mask.shape[1]) + radius + 1
    r0, r1 = np.clip(rows - radius - 1, 0, bottom), np.clip(rows + radius, 0, bottom)
    c0, c1 = np.clip(cols - radius - 1, 0, right), np.clip(cols + radius, 0, right)
    total = (
        integral[np.ix_(r1, c1)] - integral[np.ix_(r0, c1)] - integral[np.ix_(r1, c0)] + integral[np.ix_(r0, c0)]
    )
    return total / float(size * size)


def clean(mask: np.ndarray) -> np.ndarray:
    """Чистка маски движения: убрать рябь, закрыть дырки внутри силуэта."""
    opened = _box_mean(mask, 1) > 0.55
    closed = _box_mean(opened, 2) > 0.25
    return closed


def blobs(mask: np.ndarray) -> list[tuple[int, np.ndarray]]:
    """Связные пятна маски: площадь в пикселях и координаты их пикселей (N×2, строка и столбец)."""
    labels = np.zeros(mask.shape, dtype=np.int32)
    found: list[tuple[int, np.ndarray]] = []
    shifts = ((-1, 0), (1, 0), (0, -1), (0, 1), (-1, -1), (-1, 1), (1, -1), (1, 1))
    height, width = mask.shape
    for start in zip(*np.nonzero(mask)):
        if labels[start]:
            continue
        index = len(found) + 1
        queue = deque([start])
        labels[start] = index
        pixels = []
        while queue:
            row, col = queue.popleft()
            pixels.append((row, col))
            for dr, dc in shifts:
                r, c = row + dr, col + dc
                if 0 <= r < height and 0 <= c < width and mask[r, c] and not labels[r, c]:
                    labels[r, c] = index
                    queue.append((r, c))
        found.append((len(pixels), np.array(pixels, dtype=np.int32)))
    return found


class Sighting:
    """Один человек (или слипшаяся группа) на одном кадре, уже в координатах плана."""

    __slots__ = ("x", "y", "people", "zone")

    def __init__(self, x: float, y: float, people: int, zone: str | None) -> None:
        self.x, self.y, self.people, self.zone = x, y, people, zone


def _foot_point(pixels: np.ndarray) -> tuple[float, float]:
    """Точка опоры: середина нижнего края пятна — там, где человек стоит на полу."""
    bottom = pixels[:, 0].max()
    near = pixels[pixels[:, 0] >= bottom - 1]
    return float(near[:, 1].mean()), float(bottom)


def _person_area(matrix: np.ndarray, point: tuple[float, float], scale: float) -> float:
    """Сколько квадратных метров плана приходится на один пиксель кадра рядом с этой точкой."""
    x, y = point
    base = project(matrix, np.array([[x * scale, y * scale]], dtype=np.float64))[0]
    right = project(matrix, np.array([[(x + 1) * scale, y * scale]], dtype=np.float64))[0]
    down = project(matrix, np.array([[x * scale, (y + 1) * scale]], dtype=np.float64))[0]
    side_x, side_y = right - base, down - base
    return float(abs(side_x[0] * side_y[1] - side_x[1] * side_y[0]))


class ZoneLoad(BaseModel):
    """Сколько людей стояло в зоне по записи камеры."""

    zone_id: str | None = Field(default=None, description="Пусто — вне зон плана")
    label: str
    mean_people: float
    peak_people: int
    seats: int | None = Field(default=None, description="Вместимость зоны по плану, если считается")
    over_capacity_share: float | None = Field(
        default=None, description="Доля времени, когда людей больше вместимости"
    )


class Moment(BaseModel):
    second: float
    people: int


class Heatmap(BaseModel):
    """Тепловая карта посещаемости: доля кадров, в которые в ячейке кто-то стоял."""

    origin: tuple[float, float]
    cell_m: float
    cols: int
    rows: int
    values: list[float]


class VisionReport(BaseModel):
    frames: int
    duration_s: float
    people_mean: float
    people_peak: int
    zones: list[ZoneLoad]
    timeline: list[Moment]
    heatmap: Heatmap
    summary: list[str]
    assumptions: list[str]


def _zone_seats(plan: Plan, zone: Zone) -> int | None:
    """Вместимость зоны: сидячие места мебели внутри плюс место для стоящих в накопителе."""
    if zone.type not in ("waiting", "queue_accumulator"):
        return None
    shape = core.zone_polygon(zone)
    seats, occupied = 0, 0.0
    for item in core.floor_equipment(plan):
        body = core.equipment_polygon(item)
        if shape.intersects(body):
            occupied += shape.intersection(body).area
            if item.capacity_persons:
                seats += item.capacity_persons
    if zone.type == "queue_accumulator":
        seats += int(max(shape.area - occupied, 0.0) / STANDING_SQM_PER_PERSON)
    return seats or None


def analyze(
    plan: Plan,
    frames: np.ndarray,
    seconds: list[float],
    matrix: np.ndarray,
    source_size: tuple[int, int],
    person_area_sqm: float = PERSON_AREA_SQM,
) -> VisionReport:
    """Отчёт по записи камеры: загрузка зон, время и тепловая карта — всё в координатах плана."""
    if len(seconds) != len(frames):
        raise VisionError("На каждый кадр нужна его секунда записи.")
    scale = source_size[0] / frames.shape[2]  # кадр ужат: точки возвращаются к исходному размеру

    step = max(1, len(frames) // BACKGROUND_FRAMES)
    background = np.percentile(frames[::step], BACKGROUND_PERCENTILE, axis=0)

    outline = core.outline(plan)
    # Зоны вложены друг в друга (накопитель очереди лежит внутри зала), поэтому точку относим
    # к самой маленькой зоне, которая её содержит.
    zones = sorted(
        ((zone, core.zone_polygon(zone)) for zone in plan.zones), key=lambda pair: pair[1].area
    )
    bounds = outline.bounds if outline is not None else (0.0, 0.0, 10.0, 10.0)
    cols = max(1, math.ceil((bounds[2] - bounds[0]) / HEAT_CELL_M))
    rows = max(1, math.ceil((bounds[3] - bounds[1]) / HEAT_CELL_M))
    heat = np.zeros((rows, cols), dtype=np.float64)

    timeline: list[Moment] = []
    per_zone: dict[str | None, list[int]] = {}
    outside = 0
    # Площади засчитанных пятен: по ним видно, похоже ли пятно на одного человека. Если
    # типичное пятно — «трое», значит привязка растянула кадр, и счёт завышен.
    spots: list[float] = []
    for index, frame in enumerate(frames):
        mask = clean(np.abs(frame - background) > MOTION_THRESHOLD)
        counted: dict[str | None, int] = {}
        total = 0
        for size, pixels in blobs(mask):
            foot = _foot_point(pixels)
            cell = _person_area(matrix, foot, scale)
            if cell <= 0:
                continue
            area_sqm = size * cell
            if area_sqm < person_area_sqm * MIN_BLOB_SHARE:
                continue
            people = max(1, round(area_sqm / person_area_sqm))
            spots.append(area_sqm)
            x, y = project(matrix, np.array([[foot[0] * scale, foot[1] * scale]], dtype=np.float64))[0]
            if outline is not None and not outline.buffer(0.3).contains(Point(x, y)):
                outside += 1
                continue
            total += people
            point = Point(x, y)
            zone_id = next((zone.id for zone, shape in zones if shape.contains(point)), None)
            counted[zone_id] = counted.get(zone_id, 0) + people
            _splat(heat, bounds, x, y, people)
        timeline.append(Moment(second=round(seconds[index], 2), people=total))
        for zone_id in {*counted, *per_zone}:
            per_zone.setdefault(zone_id, [0] * index).append(counted.get(zone_id, 0))

    loads: list[ZoneLoad] = []
    for zone_id, values in sorted(per_zone.items(), key=lambda pair: -max(pair[1], default=0)):
        zone = next((z for z, _ in zones if z.id == zone_id), None)
        seats = _zone_seats(plan, zone) if zone is not None else None
        loads.append(
            ZoneLoad(
                zone_id=zone_id,
                label=(zone.label or zone.type) if zone is not None else "Вне зон",
                mean_people=round(float(np.mean(values)), 2),
                peak_people=int(max(values, default=0)),
                seats=seats,
                over_capacity_share=round(float(np.mean([v > seats for v in values])), 3)
                if seats
                else None,
            )
        )

    people = [moment.people for moment in timeline]
    duration = round(max(seconds) - min(seconds), 1) if seconds else 0.0
    report = VisionReport(
        frames=len(frames),
        duration_s=duration,
        people_mean=round(float(np.mean(people)), 2),
        people_peak=int(max(people, default=0)),
        zones=loads,
        timeline=timeline,
        heatmap=Heatmap(
            origin=(round(bounds[0], 3), round(bounds[1], 3)),
            cell_m=HEAT_CELL_M,
            cols=cols,
            rows=rows,
            # Два знака: карта рисуется цветом, третий знак не виден, а ответ короче втрое.
            values=[round(v, 2) for v in (heat / max(heat.max(), 1.0)).ravel().tolist()],
        ),
        summary=_summary(loads, people, duration),
        assumptions=[
            f"Человек сверху занимает {person_area_sqm:g} м² пола; в плотной очереди силуэты "
            "сливаются, и такое пятно считается по площади — число в пике это оценка снизу.",
            "Фон записи — 75-й перцентиль яркости каждого пикселя: мебель и стены уходят в фон, "
            "а посетитель, закрывающий место меньше трёх четвертей времени, остаётся виден. Тот, "
            "кто простоял на одном месте всю запись без единого движения, будет принят за мебель.",
            "Привязка к плану — по четырём точкам, отмеченным человеком; точность зависит от них.",
        ]
        + _calibration_warning(spots, person_area_sqm)
        + (
            [f"Вне контура помещения отброшено пятен: {outside}."]
            if outside
            else []
        ),
    )
    return report


# Во сколько раз типичное пятно может быть больше человека, прежде чем это перестанет
# объясняться толпой: полтора человека — ещё очередь, три — уже растянутый кадр.
SUSPICIOUS_BLOB_RATIO = 1.8


def _calibration_warning(spots: list[float], person_area_sqm: float) -> list[str]:
    """Предупреждение о неточной привязке, если типичное пятно не похоже на человека.

    Ошибиться в четырёх точках легко: отметил угол стены вместо угла пола — и кадр растянулся
    на всё помещение, каждый пиксель стал «больше» в метрах, а люди — крупнее и многочисленнее.
    Сервис не может знать правду, но может сказать, что цифры выглядят подозрительно.
    """
    if len(spots) < 10:
        return []
    typical = float(np.median(spots))
    ratio = typical / person_area_sqm
    if ratio < SUSPICIOUS_BLOB_RATIO:
        return []
    return [
        f"Типичное пятно на кадре — {ratio:.1f} человека по площади ({typical:.2f} м²). Если "
        f"плотной толпы в зале не было, привязка неточная: отмеченный на кадре участок меньше "
        f"выбранного на плане, и людей посчитано больше, чем есть. Отметьте углы заново."
    ]


def _splat(heat: np.ndarray, bounds: tuple[float, float, float, float], x: float, y: float, people: int) -> None:
    """Кладёт на карту пятно вокруг точки опоры человека.

    Вес спадает от середины к краю по колоколу: в середине человек стоял наверняка, у края —
    только мог. Без пятна карта рассыпается в отдельные ячейки, а после общего размытия
    превращается в кляксу; здесь и резкость сохраняется, и ступенек нет.
    """
    rows, cols = heat.shape
    radius = int(math.ceil(HEAT_SPOT_M / HEAT_CELL_M))
    col = int((x - bounds[0]) / HEAT_CELL_M)
    row = int((y - bounds[1]) / HEAT_CELL_M)
    c0, c1 = max(0, col - radius), min(cols, col + radius + 1)
    r0, r1 = max(0, row - radius), min(rows, row + radius + 1)
    if c0 >= c1 or r0 >= r1:
        return
    # Расстояние от центра пятна до середины каждой ячейки окна, в метрах.
    xs = (np.arange(c0, c1) + 0.5) * HEAT_CELL_M + bounds[0] - x
    ys = (np.arange(r0, r1) + 0.5) * HEAT_CELL_M + bounds[1] - y
    distance = np.hypot(xs[None, :], ys[:, None]) / HEAT_SPOT_M
    weight = np.clip(1.0 - distance**2, 0.0, 1.0) ** 2
    heat[r0:r1, c0:c1] += weight * people


def _summary(loads: list[ZoneLoad], people: list[int], duration: float) -> list[str]:
    """Выводы словами: что видно по записи."""
    lines: list[str] = []
    if people:
        lines.append(
            f"За {duration:g} с записи в зале в среднем {f'{np.mean(people):.1f}'.replace('.', ',')} посетителя, "
            f"в пике {max(people)}."
        )
    for load in loads:
        if load.zone_id is None or load.peak_people == 0:
            continue
        if load.seats and load.peak_people > load.seats:
            lines.append(
                f"{load.label}: в пике {load.peak_people} человек при вместимости {load.seats} — "
                f"мест не хватает {load.over_capacity_share:.0%} времени записи."
            )
        elif load.seats:
            lines.append(
                f"{load.label}: в пике {load.peak_people} человек при вместимости {load.seats}."
            )
        else:
            lines.append(f"{load.label}: в пике {load.peak_people} человек.")
    return lines
