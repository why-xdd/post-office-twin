"""Счёт посетителей по кадрам камеры: проверка на кадрах, где число людей известно заранее."""

from __future__ import annotations

import io
import json
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from app.models import Plan
from app.vision import counter

DATA = Path(__file__).resolve().parents[3] / "data"
FRAME = (480, 270)  # ширина и высота синтетического кадра
ROOM = (12.0, 6.75)  # какому прямоугольнику пола он соответствует


@pytest.fixture(scope="module")
def plan() -> Plan:
    return Plan.model_validate(json.loads((DATA / "demo_plan.json").read_text(encoding="utf-8")))


def make_frames(tracks: list[list[tuple[float, float]]], noise: float = 2.0) -> list[bytes]:
    """Кадры вида сверху: светлый пол и тёмные пятна людей в заданных точках (в метрах)."""
    length = len(tracks[0])
    rng = np.random.default_rng(7)
    frames: list[bytes] = []
    for step in range(length):
        picture = np.full((FRAME[1], FRAME[0]), 210.0)
        picture += rng.normal(0, noise, picture.shape)
        for track in tracks:
            x, y = track[step]
            col = int(x / ROOM[0] * FRAME[0])
            row = int(y / ROOM[1] * FRAME[1])
            half = 7  # человек сверху: пятно примерно 0,35 × 0,35 м при этом масштабе
            picture[max(row - half, 0) : row + half, max(col - half, 0) : col + half] = 60.0
        buffer = io.BytesIO()
        Image.fromarray(np.clip(picture, 0, 255).astype(np.uint8)).save(buffer, format="JPEG", quality=85)
        frames.append(buffer.getvalue())
    return frames


def straight(start: tuple[float, float], end: tuple[float, float], steps: int) -> list[tuple[float, float]]:
    return [
        (start[0] + (end[0] - start[0]) * i / (steps - 1), start[1] + (end[1] - start[1]) * i / (steps - 1))
        for i in range(steps)
    ]


@pytest.fixture(scope="module")
def matrix() -> np.ndarray:
    """Привязка: углы кадра — это углы прямоугольника пола."""
    return counter.homography(
        [(0, 0), (FRAME[0], 0), (FRAME[0], FRAME[1]), (0, FRAME[1])],
        [(0, 0), (ROOM[0], 0), (ROOM[0], ROOM[1]), (0, ROOM[1])],
    )


def test_homography_maps_corners_exactly(matrix: np.ndarray) -> None:
    points = counter.project(matrix, np.array([[0, 0], [FRAME[0], FRAME[1]], [FRAME[0] / 2, FRAME[1] / 2]]))
    assert points[0] == pytest.approx((0, 0), abs=1e-6)
    assert points[1] == pytest.approx(ROOM, abs=1e-6)
    assert points[2] == pytest.approx((ROOM[0] / 2, ROOM[1] / 2), abs=1e-6)


def test_counts_three_people(plan: Plan, matrix: np.ndarray) -> None:
    """Три человека идут по залу — столько и должно быть в пике."""
    tracks = [
        straight((2.0, 1.5), (9.0, 1.5), 24),
        straight((3.0, 4.0), (3.0, 6.0), 24),
        straight((7.0, 5.5), (10.0, 2.0), 24),
    ]
    frames, size = counter.decode_frames(make_frames(tracks))
    report = counter.analyze(plan, frames, [float(i) for i in range(24)], matrix, size)
    assert report.frames == 24
    assert report.people_peak == 3
    assert report.people_mean == pytest.approx(3.0, abs=0.35)


def test_empty_room_gives_no_people(plan: Plan, matrix: np.ndarray) -> None:
    """Пустой зал: шум камеры не должен превращаться в посетителей."""
    tracks = [straight((2.0, 1.0), (2.0, 1.0), 16)]  # один неподвижный объект уходит в фон
    frames, size = counter.decode_frames(make_frames(tracks))
    report = counter.analyze(plan, frames, [float(i) for i in range(16)], matrix, size)
    assert report.people_peak == 0


def test_heatmap_marks_where_people_stand(plan: Plan, matrix: np.ndarray) -> None:
    """Очередь стоит в одном месте — тепловая карта обязана это показать."""
    # Очередь стоит на месте, но подвигается — как в жизни.
    standing = [(4.0 + 0.12 * (i % 3), 3.0) for i in range(18)]
    walking = straight((1.0, 6.0), (11.0, 6.0), 18)
    frames, size = counter.decode_frames(make_frames([standing, walking]))
    report = counter.analyze(plan, frames, [float(i) for i in range(18)], matrix, size)
    heat = np.array(report.heatmap.values).reshape(report.heatmap.rows, report.heatmap.cols)
    col = int((4.0 - report.heatmap.origin[0]) / report.heatmap.cell_m)
    row = int((3.0 - report.heatmap.origin[1]) / report.heatmap.cell_m)
    assert heat[row - 1 : row + 2, col - 1 : col + 2].max() == pytest.approx(1.0, abs=0.01)
    assert heat.mean() < 0.05


def test_too_few_frames_is_explained() -> None:
    with pytest.raises(counter.VisionError, match="кадр"):
        counter.decode_frames([b"", b""])


def test_bad_calibration_is_explained() -> None:
    with pytest.raises(counter.VisionError, match="одной линии"):
        counter.homography([(0, 0), (10, 0), (20, 0), (30, 0)], [(0, 0), (1, 0), (2, 0), (3, 0)])


def test_matches_the_simulation_on_the_demo_recording(plan: Plan) -> None:
    """Сквозная проверка: кадры синтетической записи против правды симуляции.

    Симуляция знает, сколько людей в зале в каждую секунду. Видеоаналитика об этом не знает —
    она видит только картинку. Расхождение должно быть небольшим.
    """
    import io as _io
    import sys as _sys

    _sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
    import make_camera_sample as sample

    rng = __import__("random").Random(sample.SEED)
    scale = sample.WIDTH / plan.ops.bbox.width_m
    size = (sample.WIDTH, round(plan.ops.bbox.height_m * scale))
    base = sample.background(plan, size, scale)
    people = [sample.Visitor(path, waits) for path, waits in sample.routes(plan, rng)[:12]]
    noise = __import__("random").Random(sample.SEED + 1)

    payload: list[bytes] = []
    truth: list[int] = []
    active: list[sample.Visitor] = []
    queue = list(people)
    for second in range(45):
        if queue and second % 3 == 0:
            active.append(queue.pop(0))
        for visitor in active:
            for _ in range(sample.FPS):  # секунда записи — несколько шагов симуляции
                visitor.step(1 / sample.FPS, noise)
        active = [v for v in active if not v.done]
        frame = sample.render(plan, active, size, scale, base)
        buffer = _io.BytesIO()
        frame.resize((480, round(480 * size[1] / size[0]))).save(buffer, format="JPEG", quality=80)
        payload.append(buffer.getvalue())
        truth.append(len(active))

    frames, source = counter.decode_frames(payload)
    width, height = source
    matrix = counter.homography(
        [(0, 0), (width, 0), (width, height), (0, height)],
        [
            (0, plan.ops.bbox.height_m),
            (plan.ops.bbox.width_m, plan.ops.bbox.height_m),
            (plan.ops.bbox.width_m, 0),
            (0, 0),
        ],
    )
    report = counter.analyze(plan, frames, [float(i) for i in range(len(payload))], matrix, source)
    counted = np.array([moment.people for moment in report.timeline], dtype=float)
    real = np.array(truth, dtype=float)
    error = float(np.abs(counted - real).mean())
    assert error <= max(1.0, 0.25 * real.mean()), f"расхождение {error:.2f} при среднем {real.mean():.1f}"
    assert abs(report.people_peak - real.max()) <= 2


@pytest.fixture()
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    import importlib
    from fastapi.testclient import TestClient

    monkeypatch.setenv("OPS_TWIN_DB", str(tmp_path / "plans.sqlite"))
    import app.main as main

    importlib.reload(main)
    with TestClient(main.app) as test_client:
        yield test_client


def test_api_counts_and_explains(client, plan: Plan) -> None:
    tracks = [straight((2.0, 1.5), (9.0, 1.5), 20), straight((3.0, 4.0), (3.0, 6.0), 20)]
    payload = make_frames(tracks)
    document = json.dumps(plan.model_dump(by_alias=True, exclude_none=True), ensure_ascii=False)
    calibration = json.dumps(
        {
            "frame": [[0, 0], [FRAME[0], 0], [FRAME[0], FRAME[1]], [0, FRAME[1]]],
            "plan": [[0, 0], [ROOM[0], 0], [ROOM[0], ROOM[1]], [0, ROOM[1]]],
            "frame_size": list(FRAME),
        }
    )
    response = client.post(
        "/api/vision/analyze",
        data={"document": document, "calibration": calibration, "seconds": json.dumps(list(range(20)))},
        files=[("frames", (f"{i:03d}.jpg", frame, "image/jpeg")) for i, frame in enumerate(payload)],
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["people_peak"] == 2
    assert body["heatmap"]["cols"] > 10 and len(body["heatmap"]["values"]) == body["heatmap"]["cols"] * body["heatmap"]["rows"]
    assert any("посетител" in line for line in body["summary"])
    assert body["assumptions"]


def test_api_rejects_short_recording(client, plan: Plan) -> None:
    document = json.dumps(plan.model_dump(by_alias=True, exclude_none=True), ensure_ascii=False)
    calibration = json.dumps({"frame": [[0, 0], [1, 0], [1, 1], [0, 1]], "plan": [[0, 0], [1, 0], [1, 1], [0, 1]]})
    response = client.post(
        "/api/vision/analyze",
        data={"document": document, "calibration": calibration, "seconds": json.dumps([0, 1])},
        files=[("frames", ("a.jpg", b"x", "image/jpeg")), ("frames", ("b.jpg", b"x", "image/jpeg"))],
    )
    assert response.status_code == 422
    assert "от 8" in response.json()["detail"]
