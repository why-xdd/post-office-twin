"""Распознавание чертежа: из картинки или страницы PDF — в стены, проёмы и контур.

План отделения на чертеже — это почти всегда прямые линии по двум направлениям: длинные тёмные
полосы наружных стен, потоньше — перегородки, и разрывы в них — двери и окна. Поэтому здесь нет
ни обучения, ни внешних библиотек компьютерного зрения: тёмные пиксели считаются по строкам и
столбцам, длинные сплошные участки становятся отрезками, близкие и соосные отрезки сливаются.

Такой разбор предсказуем и объясним: видно, сколько нашлось линий, какой они длины и где в них
разрывы. Подписи оборудования и зон он не читает — это отдельный шаг (labels.py), который
подключается только при наличии языковой модели.

Масштаб чертежа неизвестен: пользователь называет действительную ширину помещения, и все
размеры пересчитываются в метры.
"""

from __future__ import annotations

import io
import math
from dataclasses import dataclass, field

import numpy as np

# Пиксель темнее этой доли от белого считается линией чертежа.
DARK_LEVEL = 0.62
# Минимальная длина линии — доля от большей стороны картинки.
MIN_LINE_SHARE = 0.06
# Разрыв короче этого (в пикселях) не считается проёмом: это шум растра или засечка.
MAX_NOISE_GAP_PX = 4
# Линии ближе этого расстояния считаются одной стеной.
MERGE_TOLERANCE_PX = 6
# Какую долю своего размаха линия должна занимать тёмным, чтобы считаться стеной, а не подписью.
MIN_LINE_COVERAGE = 0.55


@dataclass
class Segment:
    """Отрезок стены в пикселях изображения: горизонтальный или вертикальный."""

    axis: str  # "h" — горизонтальный, "v" — вертикальный
    at: float  # координата поперёк: y для горизонтального, x для вертикального
    start: float
    end: float
    thickness: float
    gaps: list[tuple[float, float]] = field(default_factory=list)

    @property
    def length(self) -> float:
        return self.end - self.start


@dataclass
class Detection:
    """Что нашлось на чертеже, в пикселях."""

    width_px: int
    height_px: int
    segments: list[Segment]
    dark_share: float

    @property
    def bounds(self) -> tuple[float, float, float, float]:
        xs: list[float] = []
        ys: list[float] = []
        for segment in self.segments:
            if segment.axis == "h":
                xs.extend([segment.start, segment.end])
                ys.append(segment.at)
            else:
                ys.extend([segment.start, segment.end])
                xs.append(segment.at)
        if not xs or not ys:
            return 0.0, 0.0, float(self.width_px), float(self.height_px)
        return min(xs), min(ys), max(xs), max(ys)


def to_gray(data: bytes, page: int = 0, target_width: int = 1400) -> np.ndarray:
    """Картинка или страница PDF — в матрицу яркости от 0 (чёрный) до 1 (белый)."""
    from PIL import Image

    if data[:5] == b"%PDF-":
        import pypdfium2

        document = pypdfium2.PdfDocument(io.BytesIO(data))
        try:
            if page >= len(document):
                raise ValueError(f"В файле {len(document)} страниц, запрошена {page + 1}-я.")
            rendered = document[page].render(scale=2.0)
            image = rendered.to_pil()
        finally:
            document.close()
    else:
        image = Image.open(io.BytesIO(data))

    image = image.convert("L")
    if image.width > target_width:
        height = max(1, round(image.height * target_width / image.width))
        image = image.resize((target_width, height), Image.LANCZOS)
    return np.asarray(image, dtype=np.float32) / 255.0


def _runs(mask: np.ndarray) -> list[tuple[int, int]]:
    """Непрерывные участки True в одномерной маске: (начало, конец включительно)."""
    if not mask.any():
        return []
    padded = np.concatenate(([False], mask, [False]))
    changes = np.flatnonzero(padded[1:] != padded[:-1])
    return [(int(changes[i]), int(changes[i + 1]) - 1) for i in range(0, len(changes), 2)]


def _line_segments(dark: np.ndarray, axis: str, min_length: int) -> list[Segment]:
    """Длинные тёмные полосы вдоль оси вместе с проёмами внутри них.

    Стена почти сплошная: сначала берётся размах от первого до последнего тёмного пикселя в
    строке, затем проверяется, какую долю этого размаха занимает тёмное. Светлые промежутки
    внутри — это двери и окна, а не конец стены.
    """
    lines = dark if axis == "h" else dark.T
    found: list[Segment] = []
    for index, row in enumerate(lines):
        runs = _runs(row)
        if not runs:
            continue
        span_start, span_end = runs[0][0], runs[-1][1]
        span = span_end - span_start + 1
        if span < min_length:
            continue
        covered = sum(end - start + 1 for start, end in runs)
        if covered / span < MIN_LINE_COVERAGE:
            continue
        gaps = [
            (float(previous[1] + 1), float(current[0] - 1))
            for previous, current in zip(runs, runs[1:])
            if current[0] - previous[1] - 1 > MAX_NOISE_GAP_PX
        ]
        found.append(
            Segment(axis=axis, at=float(index), start=float(span_start), end=float(span_end), thickness=1.0, gaps=gaps)
        )
    return found


def _merge(segments: list[Segment]) -> list[Segment]:
    """Соседние параллельные полосы — это одна стена: они сливаются, толщина суммируется."""
    result: list[Segment] = []
    for segment in sorted(segments, key=lambda s: (s.at, s.start)):
        target = None
        for candidate in result:
            if abs(candidate.at - segment.at) > MERGE_TOLERANCE_PX:
                continue
            if segment.start > candidate.end + MERGE_TOLERANCE_PX or segment.end < candidate.start - MERGE_TOLERANCE_PX:
                continue
            target = candidate
            break
        if target is None:
            result.append(Segment(**{**segment.__dict__, "gaps": list(segment.gaps)}))
            continue
        span = target.end - target.start + segment.end - segment.start
        weight = (target.end - target.start) / span if span else 0.5
        target.at = target.at * weight + segment.at * (1 - weight)
        target.start = min(target.start, segment.start)
        target.end = max(target.end, segment.end)
        target.thickness += 1.0
        # Проёмом считается разрыв, который есть во всех слоях стены.
        target.gaps = [
            (max(a1, a2), min(b1, b2))
            for (a1, b1) in target.gaps
            for (a2, b2) in segment.gaps
            if max(a1, a2) < min(b1, b2)
        ]
    return result


def detect(data: bytes, page: int = 0) -> Detection:
    """Стены и проёмы на чертеже, в пикселях изображения."""
    gray = to_gray(data, page)
    dark = gray < DARK_LEVEL
    min_length = max(20, int(max(gray.shape) * MIN_LINE_SHARE))
    segments = _merge(_line_segments(dark, "h", min_length)) + _merge(_line_segments(dark, "v", min_length))
    # Слишком короткие после слияния линии — подписи и штриховка, а не стены.
    segments = [s for s in segments if s.length >= min_length]
    return Detection(
        width_px=int(gray.shape[1]),
        height_px=int(gray.shape[0]),
        segments=segments,
        dark_share=float(dark.mean()),
    )


def outer_walls(detection: Detection) -> list[Segment]:
    """Самые длинные линии по краям — наружные стены помещения."""
    if not detection.segments:
        return []
    min_x, min_y, max_x, max_y = detection.bounds
    width = max(max_x - min_x, 1.0)
    height = max(max_y - min_y, 1.0)
    edge = 0.12
    walls: list[Segment] = []
    for segment in detection.segments:
        if segment.axis == "h":
            near_edge = abs(segment.at - min_y) < height * edge or abs(segment.at - max_y) < height * edge
            long_enough = segment.length > width * 0.6
        else:
            near_edge = abs(segment.at - min_x) < width * edge or abs(segment.at - max_x) < width * edge
            long_enough = segment.length > height * 0.6
        if near_edge and long_enough:
            walls.append(segment)
    return walls


def scale_factor(detection: Detection, real_width_m: float) -> float:
    """Сколько метров в одном пикселе: пользователь называет действительную ширину помещения."""
    min_x, _, max_x, _ = detection.bounds
    span = max(max_x - min_x, 1.0)
    return real_width_m / span


def summary(detection: Detection) -> dict[str, float | int]:
    """Числа для человека: сколько линий нашлось и какой они длины."""
    horizontal = [s for s in detection.segments if s.axis == "h"]
    vertical = [s for s in detection.segments if s.axis == "v"]
    lengths = [s.length for s in detection.segments] or [0.0]
    return {
        "линий_всего": len(detection.segments),
        "горизонтальных": len(horizontal),
        "вертикальных": len(vertical),
        "самая_длинная_px": round(max(lengths), 1),
        "средняя_длина_px": round(sum(lengths) / len(lengths), 1),
        "доля_тёмного": round(detection.dark_share, 4),
        "ширина_px": detection.width_px,
        "высота_px": detection.height_px,
    }


def angle_of(segment: Segment) -> float:
    """Наклон отрезка в градусах — пригодится, когда чертёж снят под углом."""
    return 0.0 if segment.axis == "h" else 90.0


def distance(a: tuple[float, float], b: tuple[float, float]) -> float:
    return math.hypot(a[0] - b[0], a[1] - b[1])
