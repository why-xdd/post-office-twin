"""Из распознанных линий — в план отделения.

Изображение и план живут в разных системах координат: у картинки начало в левом верхнем углу и
ось Y вниз, у плана — в левом нижнем и ось Y вверх. Здесь линии переводятся в метры, собираются
в контур помещения и перегородки, а разрывы в стенах становятся дверями и окнами.

Результат — черновик: он открывается в редакторе, где человек поправляет размеры и подписи.
Поэтому важнее не угадать всё, а не соврать: всё сомнительное попадает в список замечаний.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from app.dictionaries import equipment_types, zone_types
from app.importer import labels
from app.importer.drawing import Detection, Segment, detect, outer_walls, scale_factor, summary
from app.models import Plan

# Проём уже этого в метрах — скорее разрыв линии на чертеже, чем дверь.
MIN_OPENING_M = 0.6
MAX_OPENING_M = 4.0
# Дверь наружу обычно уже, чем витрина: более широкие проёмы считаются окнами.
DOOR_LIMIT_M = 2.2


class ImportResult(BaseModel):
    """Черновик плана и честный рассказ о том, что распозналось."""

    document: dict[str, Any] = Field(description="План в формате сервиса — черновик для редактора")
    notes: list[str] = Field(default_factory=list, description="Что стоит проверить человеку")
    stats: dict[str, Any] = Field(default_factory=dict, description="Числа распознавания")
    labels_source: str = Field(default="", description="Чем подписаны зоны и оборудование")


def _metres(value: float, digits: int = 2) -> str:
    """Число в метрах по-русски: с запятой и без лишних нулей."""
    return f"{round(value, digits):g}".replace(".", ",")


def _outline_rect(walls: list[Segment], detection: Detection) -> tuple[float, float, float, float]:
    """Прямоугольник помещения по крайним наружным линиям, в пикселях."""
    horizontal = [w for w in walls if w.axis == "h"]
    vertical = [w for w in walls if w.axis == "v"]
    min_x, min_y, max_x, max_y = detection.bounds
    top = min((w.at for w in horizontal), default=min_y)
    bottom = max((w.at for w in horizontal), default=max_y)
    left = min((w.at for w in vertical), default=min_x)
    right = max((w.at for w in vertical), default=max_x)
    return left, top, right, bottom


def build(
    data: bytes,
    real_width_m: float,
    page: int = 0,
    name: str = "План с чертежа",
    media_type: str = "image/png",
    read_labels: bool = True,
) -> ImportResult:
    """Распознаёт чертёж и собирает из него черновик плана.

    Геометрию считает разбор изображения. Подписи оборудования и зон читает языковая модель,
    если она подключена: без неё импорт всё равно даёт готовый контур со стенами и проёмами.
    """
    detection = detect(data, page)
    notes: list[str] = []
    stats = summary(detection)
    if not detection.segments:
        return ImportResult(
            document={},
            notes=["На изображении не нашлось прямых линий: нужен чертёж плана, а не фотография зала."],
            stats=stats,
        )

    walls = outer_walls(detection)
    if len({w.axis for w in walls}) < 2:
        notes.append("Контур помещения собран по крайним линиям чертежа — проверьте размеры стен.")
    left, top, right, bottom = _outline_rect(walls, detection)
    scale = scale_factor(detection, real_width_m)
    width_m = (right - left) * scale
    depth_m = (bottom - top) * scale
    if depth_m <= 0.5 or width_m <= 0.5:
        return ImportResult(
            document={},
            notes=["Не удалось определить контур помещения: линии слишком короткие или обрезаны."],
            stats=stats,
        )

    def to_plan(x_px: float, y_px: float) -> list[float]:
        """Пиксели картинки — в метры плана: начало в левом нижнем углу, ось Y вверх."""
        return [round((x_px - left) * scale, 3), round((bottom - y_px) * scale, 3)]

    outline = [
        to_plan(left, bottom),
        to_plan(right, bottom),
        to_plan(right, top),
        to_plan(left, top),
    ]
    document: dict[str, Any] = {
        "ops": {
            "id": f"ops_import_{abs(hash((width_m, depth_m))) % 10**8}",
            "name": name,
            "scale": "1 unit = 1 meter",
            "bbox": {"width_m": round(width_m, 2), "height_m": round(depth_m, 2)},
        },
        "walls": [
            {"id": "w_s", "from": outline[0], "to": outline[1], "type": "outer"},
            {"id": "w_e", "from": outline[1], "to": outline[2], "type": "outer"},
            {"id": "w_n", "from": outline[2], "to": outline[3], "type": "outer"},
            {"id": "w_w", "from": outline[3], "to": outline[0], "type": "outer"},
        ],
        "doors": [],
        "windows": [],
        "zones": [
            {
                "id": "z_client",
                "type": "client_area",
                "label": "Клиентская зона",
                "polygon": outline,
            }
        ],
        "equipment": [],
    }

    # Внутренние длинные линии — перегородки.
    partitions = 0
    for segment in detection.segments:
        if segment in walls:
            continue
        inside = (
            left + 4 < segment.at < right - 4 if segment.axis == "v" else top + 4 < segment.at < bottom - 4
        )
        if not inside or segment.length * scale < 1.0:
            continue
        partitions += 1
        start = (segment.start, segment.at) if segment.axis == "h" else (segment.at, segment.start)
        end = (segment.end, segment.at) if segment.axis == "h" else (segment.at, segment.end)
        document["walls"].append(
            {
                "id": f"w_partition_{partitions}",
                "from": to_plan(*start),
                "to": to_plan(*end),
                "type": "partition",
                "label": "Перегородка",
            }
        )

    # Разрывы в стенах — двери и окна.
    openings = 0
    for segment in walls + [s for s in detection.segments if s not in walls]:
        for gap_start, gap_end in segment.gaps:
            size_m = (gap_end - gap_start) * scale
            if size_m < MIN_OPENING_M or size_m > MAX_OPENING_M:
                continue
            openings += 1
            if segment.axis == "h":
                a, b = to_plan(gap_start, segment.at), to_plan(gap_end, segment.at)
            else:
                a, b = to_plan(segment.at, gap_start), to_plan(segment.at, gap_end)
            if size_m <= DOOR_LIMIT_M:
                door_type = "entrance" if not document["doors"] else "internal"
                document["doors"].append(
                    {
                        "id": f"d_{openings}",
                        "wall": [*a, *b],
                        "type": door_type,
                        "width_m": round(size_m, 2),
                        "label": "Вход" if door_type == "entrance" else "Проём",
                    }
                )
            else:
                document["windows"].append({"id": f"win_{openings}", "wall": [*a, *b], "height_m": 1.2})

    if document["doors"]:
        document["flow_hints"] = {"main_entrance": document["doors"][0]["id"], "typical_paths": []}
    else:
        notes.append("Проёмов в стенах не нашлось — отметьте вход двойным щелчком по стене.")
    notes.append(
        "Размеры пересчитаны из ширины "
        + _metres(real_width_m)
        + " м: помещение "
        + _metres(width_m)
        + " × "
        + _metres(depth_m)
        + " м, площадь "
        + _metres(width_m * depth_m, 1)
        + " м²."
    )
    if partitions:
        notes.append(f"Найдено перегородок: {partitions}. Проверьте их длину и положение.")
    notes.append("Оборудование и зоны чертёж не содержит в машинном виде — расставьте их в редакторе.")

    labels_source = ""
    if read_labels and data[:5] != b"%PDF-":
        found = labels.read(data, media_type, _catalogue())
        if found:
            labels_source = "языковая модель прочитала подписи чертежа"
            notes.extend(
                labels.apply(
                    document,
                    found,
                    to_plan,
                    (detection.width_px, detection.height_px),
                    equipment_types(None),
                    zone_types(None),
                )
            )

    stats["перегородок"] = partitions
    stats["проёмов"] = len(document["doors"]) + len(document["windows"])
    stats["объектов"] = len(document["equipment"])
    # Черновик обязан быть корректным планом: иначе редактор его не откроет.
    Plan.model_validate(document)
    return ImportResult(document=document, notes=notes, stats=stats, labels_source=labels_source)


def _catalogue() -> dict[str, Any]:
    """Справочник для модели: коды и названия типов оборудования и зон."""
    return {
        "типы_оборудования": {code: t.label for code, t in equipment_types(None).items()},
        "типы_зон": {code: z.label for code, z in zone_types(None).items()},
    }
