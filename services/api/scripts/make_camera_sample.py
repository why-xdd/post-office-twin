"""Синтетическая запись камеры зала для демонстрации видеоаналитики.

Настоящих записей из отделения у нас нет и быть не может, поэтому запись моделируется: по
демонстрационному плану ходят посетители — входят, встают в накопитель очереди, обслуживаются у
окна и уходят. Камера условная, вид сверху, как у потолочной камеры с широким углом.

Из этой записи видеоаналитика должна получить то же, что знает симуляция: сколько людей стоит в
зале и в очереди. Поэтому файл годится и как демонстрация, и как проверка.

Запуск из services/api (нужен ffmpeg):
    uv run python scripts/make_camera_sample.py
"""

from __future__ import annotations

import argparse
import json
import math
import random
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from PIL import Image, ImageDraw
from shapely.geometry import Point as ShapelyPoint

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.geometry import core  # noqa: E402
from app.models import Plan  # noqa: E402

ROOT = Path(__file__).resolve().parents[3]
DEMO = ROOT / "data" / "demo_plan.json"
OUT = ROOT / "data" / "camera_sample.mp4"
TRUTH = ROOT / "data" / "camera_sample_truth.json"
SEED = 20261001
FPS = 4
SECONDS = 90
WIDTH = 960
# Цвета «как с дешёвой камеры»: пол светлый, мебель и стены тёмные, люди тёмные пятна.
FLOOR, WALL, FURNITURE, PERSON, ZONE = 214, 40, 96, 52, 205
PERSON_RADIUS_M = 0.22
STEP_M = 0.9 / FPS  # человек идёт примерно 0,9 м/с
ARRIVAL_EVERY_S = 4.5


class Visitor:
    """Посетитель: идёт по точкам маршрута и стоит там, где положено ждать."""

    def __init__(self, path: list[tuple[float, float]], waits: list[float]) -> None:
        self.path, self.waits = path, waits
        self.index = 0
        self.position = path[0]
        self.wait_left = 0.0
        self.done = False

    def step(self, dt: float, jitter: random.Random) -> None:
        if self.done:
            return
        if self.wait_left > 0:
            self.wait_left -= dt
            # Стоящий в очереди переминается: камера видит движение, а не мебель.
            self.position = (
                self.position[0] + jitter.uniform(-0.04, 0.04),
                self.position[1] + jitter.uniform(-0.04, 0.04),
            )
            return
        target = self.path[self.index + 1]
        dx, dy = target[0] - self.position[0], target[1] - self.position[1]
        distance = math.hypot(dx, dy)
        if distance <= STEP_M:
            self.position = target
            self.wait_left = self.waits[self.index]
            self.index += 1
            if self.index >= len(self.path) - 1:
                self.done = True
            return
        self.position = (
            self.position[0] + dx / distance * STEP_M,
            self.position[1] + dy / distance * STEP_M,
        )


def routes(plan: Plan, rng: random.Random) -> list[tuple[list[tuple[float, float]], list[float]]]:
    """Маршруты: вход → место в очереди → окно (или стойка выдачи) → выход."""
    entrance = None
    for wall in plan.walls:
        if wall.type == "door" or (plan.flow_hints and wall.id == plan.flow_hints.main_entrance):
            entrance = core.door_center((wall.from_[0], wall.from_[1], wall.to[0], wall.to[1]))
    if entrance is None:
        entrance = (plan.ops.bbox.width_m / 2, 0.3)

    queue_zone = next((z for z in plan.zones if z.type == "queue_accumulator"), None)
    spots: list[tuple[float, float]] = []
    if queue_zone is not None:
        shape = core.zone_polygon(queue_zone)
        minx, miny, maxx, maxy = shape.bounds
        step = 0.7
        y = miny + 0.4
        while y < maxy - 0.2:
            spots.append(((minx + maxx) / 2, y))
            y += step
    windows = core.equipment_by_type(plan, "cashier_window", "pvz_counter")
    made = []
    for _ in range(60):
        target = rng.choice(windows) if windows else None
        service = target.position if target is not None else (entrance[0], entrance[1] + 3)
        queue_spot = rng.choice(spots) if spots else service
        path = [entrance, queue_spot, (service[0], service[1] + 1.0), entrance]
        waits = [rng.uniform(8, 30), rng.uniform(40, 120), 0.0]
        made.append((path, waits))
    return made


def render(plan: Plan, visitors: list[Visitor], size: tuple[int, int], scale: float, base: Image.Image) -> Image.Image:
    frame = base.copy()
    draw = ImageDraw.Draw(frame)
    radius = PERSON_RADIUS_M * scale
    for visitor in visitors:
        x, y = visitor.position
        cx, cy = x * scale, size[1] - y * scale
        draw.ellipse([cx - radius, cy - radius, cx + radius, cy + radius], fill=PERSON)
    return frame


def background(plan: Plan, size: tuple[int, int], scale: float) -> Image.Image:
    """Пустой зал: пол, зоны, мебель, стены. Всё, что не двигается."""
    frame = Image.new("L", size, FLOOR)
    draw = ImageDraw.Draw(frame)
    flip = lambda point: (point[0] * scale, size[1] - point[1] * scale)  # noqa: E731
    for zone in plan.zones:
        draw.polygon([flip(p) for p in zone.polygon], fill=ZONE)
    for item in core.floor_equipment(plan):
        draw.polygon([flip(p) for p in core.equipment_polygon(item).exterior.coords], fill=FURNITURE)
    for wall in plan.walls:
        width = 5 if wall.type == "outer" else 3
        draw.line([flip(wall.from_), flip(wall.to)], fill=WALL, width=width)
    return frame


def main() -> None:
    parser = argparse.ArgumentParser(description="Синтетическая запись камеры зала")
    parser.add_argument("--out", default=str(OUT))
    parser.add_argument("--seconds", type=int, default=SECONDS)
    args = parser.parse_args()
    if shutil.which("ffmpeg") is None:
        raise SystemExit("Нужен ffmpeg: запись собирается из кадров им.")

    plan = Plan.model_validate(json.loads(DEMO.read_text(encoding="utf-8")))
    rng = random.Random(SEED)
    scale = WIDTH / plan.ops.bbox.width_m
    size = (WIDTH, round(plan.ops.bbox.height_m * scale))
    base = background(plan, size, scale)
    plans = routes(plan, rng)

    active: list[Visitor] = []
    pending = list(plans)
    # Правда симуляции: сколько людей в зале и в каждой зоне в каждую секунду. По ней потом
    # проверяется, насколько точно считает видеоаналитика.
    truth: list[dict] = []
    zones = [(zone, core.zone_polygon(zone)) for zone in plan.zones]
    zones.sort(key=lambda pair: pair[1].area)
    frames = args.seconds * FPS
    noise = random.Random(SEED + 1)
    with tempfile.TemporaryDirectory() as folder:
        for number in range(frames):
            second = number / FPS
            if pending and second >= len(plans) - len(pending) and second % ARRIVAL_EVERY_S < 1 / FPS:
                path, waits = pending.pop(0)
                active.append(Visitor(path, waits))
            for visitor in active:
                visitor.step(1 / FPS, noise)
            active = [v for v in active if not v.done]
            frame = render(plan, active, size, scale, base)
            frame.save(Path(folder) / f"{number:05d}.png")
            if number % FPS == 0:
                counts: dict[str, int] = {}
                for visitor in active:
                    point = ShapelyPoint(visitor.position)
                    zone_id = next((z.id for z, shape in zones if shape.contains(point)), "вне зон")
                    counts[zone_id] = counts.get(zone_id, 0) + 1
                truth.append({"second": second, "people": len(active), "zones": counts})
        command = [
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
            "-framerate", str(FPS), "-i", str(Path(folder) / "%05d.png"),
            "-c:v", "libx264", "-preset", "slow", "-crf", "30", "-pix_fmt", "yuv420p",
            "-movflags", "+faststart", args.out,
        ]
        subprocess.run(command, check=True)
    TRUTH.write_text(json.dumps(truth, ensure_ascii=False, indent=1), encoding="utf-8")
    peak = max((moment["people"] for moment in truth), default=0)
    print(f"{args.out}: {frames} кадров, {args.seconds} с, {size[0]}×{size[1]}; в пике {peak} человек")
    print(f"{TRUTH}: посекундная правда симуляции")


if __name__ == "__main__":
    main()
