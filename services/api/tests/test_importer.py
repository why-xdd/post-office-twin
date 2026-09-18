"""Импорт чертежа: из картинки — в план с теми же размерами, что были нарисованы."""

from __future__ import annotations

import io

import pytest

from app.importer import build as importer
from app.importer import drawing


def draw_plan(width_m: float = 12.0, depth_m: float = 8.0, scale_px: int = 60) -> bytes:
    """Синтетический чертёж: прямоугольник, перегородка и два проёма в известных местах."""
    from PIL import Image, ImageDraw

    margin = 40
    width_px = int(width_m * scale_px) + margin * 2
    height_px = int(depth_m * scale_px) + margin * 2
    image = Image.new("L", (width_px, height_px), 255)
    draw = ImageDraw.Draw(image)
    left, top = margin, margin
    right, bottom = margin + int(width_m * scale_px), margin + int(depth_m * scale_px)

    thickness = 6
    draw.rectangle([left, top, right, bottom], outline=0, width=thickness)
    # Перегородка: вертикальная линия на 3,5 м от правой стены.
    partition_x = right - int(3.5 * scale_px)
    draw.line([partition_x, top, partition_x, bottom], fill=0, width=3)
    # Вход шириной 1,5 м в нижней стене и проём 0,9 м в перегородке.
    door_start = left + int(2.0 * scale_px)
    draw.rectangle([door_start, bottom - thickness, door_start + int(1.5 * scale_px), bottom + thickness], fill=255)
    gap_start = top + int(3.0 * scale_px)
    draw.rectangle([partition_x - 3, gap_start, partition_x + 3, gap_start + int(0.9 * scale_px)], fill=255)

    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


@pytest.fixture()
def client_and_drawing(tmp_path, monkeypatch):
    """Сервис на временной базе и готовый синтетический чертёж."""
    import importlib

    from fastapi.testclient import TestClient

    monkeypatch.setenv("OPS_TWIN_DB", str(tmp_path / "plans.sqlite"))
    import app.main as main

    importlib.reload(main)
    with TestClient(main.app) as client:
        yield client, draw_plan()


def test_detects_walls_and_openings() -> None:
    detection = drawing.detect(draw_plan())
    assert detection.segments
    walls = drawing.outer_walls(detection)
    assert len([w for w in walls if w.axis == "h"]) >= 2
    assert len([w for w in walls if w.axis == "v"]) >= 2


def test_builds_a_plan_with_the_stated_size() -> None:
    """Пользователь называет ширину помещения — остальные размеры считаются от неё."""
    result = importer.build(draw_plan(width_m=12.0, depth_m=8.0), real_width_m=12.0, name="Чертёж")
    assert result.document, result.notes
    bbox = result.document["ops"]["bbox"]
    assert bbox["width_m"] == pytest.approx(12.0, abs=0.3)
    assert bbox["height_m"] == pytest.approx(8.0, abs=0.3)
    assert len([w for w in result.document["walls"] if w["type"] == "outer"]) == 4
    assert any(w["type"] == "partition" for w in result.document["walls"])


def test_finds_the_entrance_opening() -> None:
    result = importer.build(draw_plan(), real_width_m=12.0)
    doors = result.document["doors"]
    assert doors, result.notes
    entrance = doors[0]
    assert entrance["type"] == "entrance"
    assert entrance["width_m"] == pytest.approx(1.5, abs=0.25)


def test_photo_without_lines_is_refused_politely() -> None:
    from PIL import Image

    noise = Image.new("L", (300, 200), 255)
    buffer = io.BytesIO()
    noise.save(buffer, format="PNG")
    result = importer.build(buffer.getvalue(), real_width_m=10.0)
    assert result.document == {}
    assert result.notes and "линий" in result.notes[0]


def test_import_endpoint_returns_a_draft(client_and_drawing) -> None:
    """Загрузка чертежа возвращает черновик плана и понятные замечания, ничего не сохраняя."""
    client, drawing_bytes = client_and_drawing
    response = client.post(
        "/api/import/drawing",
        files={"file": ("plan.png", drawing_bytes, "image/png")},
        data={"real_width_m": "12", "name": "С чертежа", "read_labels": "false"},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["document"]["ops"]["name"] == "С чертежа"
    assert body["notes"]
    assert body["stats"]["линий_всего"] > 0
    # Сохранённых планов не прибавилось: импорт только предлагает черновик.
    assert client.get("/api/plans").json() == []


def test_import_endpoint_rejects_a_text_file(client_and_drawing) -> None:
    client, _ = client_and_drawing
    response = client.post(
        "/api/import/drawing",
        files={"file": ("note.txt", b"just text", "text/plain")},
        data={"real_width_m": "12"},
    )
    assert response.status_code == 422
    assert "PNG" in response.json()["detail"]
