"""Устойчивость к кривым данным: сервис объясняет проблему, а не падает.

Каждая проверка здесь выросла из настоящей поломки, найденной при разборе сервиса: план с «nan»
в координате, зона-восьмёрка, картинка вместо таблицы, привязка камеры из неверных полей.
"""

from __future__ import annotations

import importlib
import json
from pathlib import Path
from typing import Iterator

import pytest
from fastapi.testclient import TestClient

from app.assistant import recommend
from app.models import Plan
from app.rules.engine import validate

DATA = Path(__file__).resolve().parents[3] / "data"
DEMO = json.loads((DATA / "demo_plan.json").read_text(encoding="utf-8"))


@pytest.fixture()
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    monkeypatch.setenv("OPS_TWIN_DB", str(tmp_path / "plans.sqlite"))
    import app.main as main

    importlib.reload(main)
    with TestClient(main.app) as test_client:
        yield test_client


def plan_with(**changes: object) -> dict:
    document = json.loads(json.dumps(DEMO))
    for key, value in changes.items():
        document[key] = value
    return document


def test_not_a_number_in_coordinate_is_refused(client: TestClient) -> None:
    """«nan» в координате не должен доходить до геометрии: там он ломает всё и навсегда."""
    document = plan_with()
    document["equipment"][0]["position"] = ["nan", 1.0]
    answer = client.post("/api/validate", json={"document": document})
    assert answer.status_code == 422
    saved = client.post("/api/plans", json={"title": "Кривой", "document": document})
    assert saved.status_code == 422, "такой план нельзя и сохранять — иначе он не откроется"


def test_endless_coordinate_is_refused(client: TestClient) -> None:
    """В JSON бесконечность пишут литералом Infinity — его тоже нельзя пускать в расчёт."""
    document = plan_with()
    document["equipment"][0]["position"] = ["__INF__", 1.0]
    body = json.dumps({"document": document}).replace('"__INF__"', "Infinity")
    answer = client.post("/api/metrics", content=body, headers={"Content-Type": "application/json"})
    assert answer.status_code == 422


def test_zero_and_negative_size_are_refused(client: TestClient) -> None:
    """Объект нулевого или отрицательного размера не существует, а площади по нему считались."""
    for size in ([0, 1], [-3, -3]):
        document = plan_with()
        document["equipment"][0]["size_m"] = size
        assert client.post("/api/validate", json={"document": document}).status_code == 422


def test_self_crossing_zone_is_reported_not_crashing(client: TestClient) -> None:
    """Зона, нарисованная «восьмёркой», раньше роняла проверку."""
    document = plan_with()
    document["zones"].append(
        {"id": "z_bad", "type": "waiting", "label": "Кривая зона", "polygon": [[0, 0], [10, 8], [10, 0], [0, 8]]}
    )
    answer = client.post("/api/validate", json={"document": document})
    assert answer.status_code == 200
    report = answer.json()
    assert any(finding["rule_id"] == "zone_polygon_valid" for finding in report["findings"])


def test_picture_instead_of_table_is_explained(client: TestClient) -> None:
    """В поля для CSV присылают что угодно: должно быть 422 с человеческим текстом."""
    picture = b"\x89PNG\r\n\x1a\n" + bytes(range(256)) * 8
    document = json.dumps(DEMO, ensure_ascii=False)
    for path in ("/api/exchange/1c/reconcile", "/api/capacity/log"):
        answer = client.post(
            path, data={"document": document}, files={"file": ("снимок.png", picture, "image/png")}
        )
        assert answer.status_code == 422, path
        assert "UTF-8" in answer.json()["detail"] or "формат" in answer.json()["detail"]


def test_broken_camera_calibration_is_explained(client: TestClient) -> None:
    frames = [("frames", (f"{i}.jpg", b"\xff\xd8\xff", "image/jpeg")) for i in range(8)]
    document = json.dumps(DEMO, ensure_ascii=False)
    for calibration in ('{"frame": 5, "plan": 5}', '{"frame": [[0,0,0]], "plan": [[0,0,0]]}'):
        answer = client.post(
            "/api/vision/analyze",
            data={"document": document, "calibration": calibration, "seconds": json.dumps(list(range(8)))},
            files=frames,
        )
        assert answer.status_code == 422, calibration


def test_missing_version_says_which_version(client: TestClient) -> None:
    """«План не найден» на существующем плане сбивает с толку: не найдена версия."""
    created = client.post("/api/plans", json={"title": "Демо", "document": DEMO}).json()
    answer = client.get(f"/api/plans/{created['id']}?version=99")
    assert answer.status_code == 404
    assert "верси" in answer.json()["detail"].lower()


def test_applying_a_removal_actually_removes() -> None:
    """Рекомендация «убрать объект» обязана убирать его из плана."""
    plan = Plan.model_validate(DEMO)
    victim = plan.equipment[0].id
    change = recommend.Change(op="remove", object_id=victim, equipment_type=plan.equipment[0].type)
    after = recommend.apply_changes(plan, [change])
    assert victim not in {item.id for item in after.equipment}
    assert len(after.equipment) == len(plan.equipment) - 1


def test_moving_out_of_a_zone_clears_the_link() -> None:
    """Объект, уехавший из зоны, не должен оставаться к ней привязанным."""
    plan = Plan.model_validate(DEMO)
    item = next(equipment for equipment in plan.equipment if equipment.zone)
    outside = (plan.ops.bbox.width_m - 0.3, plan.ops.bbox.height_m - 0.3)
    after = recommend.apply_changes(
        plan,
        [recommend.Change(op="move", object_id=item.id, equipment_type=item.type, position=outside)],
    )
    moved = next(equipment for equipment in after.equipment if equipment.id == item.id)
    assert moved.zone != item.zone
    assert not any(
        finding.rule_id == "zone_links" and item.id in finding.objects for finding in validate(after).findings
    )


def test_missing_plan_with_version_is_still_not_found(client: TestClient) -> None:
    """Несуществующий план с номером версии не должен ронять обработчик."""
    answer = client.get("/api/plans/нет-такого-плана?version=2")
    assert answer.status_code == 404
    assert "не найден" in answer.json()["detail"]


def test_objects_flush_together_are_a_violation() -> None:
    """Между рабочими местами, поставленными вплотную, прохода нет — это нарушение."""
    from app.geometry import core

    plan = Plan.model_validate(DEMO)
    document = plan.model_dump(by_alias=True, exclude_none=True)
    first, second = document["equipment"][0], document["equipment"][1]
    first["position"], first["rotation_deg"] = [3.0, 3.0], 0
    second["position"], second["rotation_deg"] = [3.0 + first["size_m"][0], 3.0], 0
    tight = core.clearances(Plan.model_validate(document), 1.0)
    assert any({gap.first_id, gap.second_id} == {first["id"], second["id"]} for gap in tight)


def test_wall_mounted_equipment_is_not_called_outside() -> None:
    """Камера и извещатель висят на стене и на потолке: они не «за пределами помещения»."""
    from app.geometry import core

    plan = Plan.model_validate(DEMO)
    assert core.objects_outside(plan) == []


def test_equipment_carried_out_of_the_building_is_found() -> None:
    from app.geometry import core

    plan = Plan.model_validate(DEMO)
    document = plan.model_dump(by_alias=True, exclude_none=True)
    camera = next(item for item in document["equipment"] if item["type"] == "camera")
    camera["position"] = [plan.ops.bbox.width_m + 3, 2.0]
    assert camera["id"] in core.objects_outside(Plan.model_validate(document))
