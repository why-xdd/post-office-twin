"""Кэш геометрии не должен отвечать за прошлую расстановку.

Свободная площадь, сужения проходов, точка входа и круг разворота считаются один раз и
запоминаются: без этого один запрос к ассистенту считал бы одно и то же по десять раз. Цена
такого ускорения — риск получить ответ для другого плана, и каждая проверка здесь закрывает
один такой случай: сдвинули объект, убрали его из расчёта, перенесли вход, обрезали зону.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.geometry import passability
from app.models import Plan
from app.rules.engine import VISITOR_TARGET_TYPES

DATA = Path(__file__).resolve().parents[3] / "data"
DEMO = json.loads((DATA / "demo_plan.json").read_text(encoding="utf-8"))


def changed(**fields: object) -> dict:
    return {**json.loads(json.dumps(DEMO)), **fields}


def moved(item_id: str, position: tuple[float, float]) -> Plan:
    document = json.loads(json.dumps(DEMO))
    for item in document["equipment"]:
        if item["id"] == item_id:
            item["position"] = list(position)
    return Plan.model_validate(document)


@pytest.fixture()
def demo() -> Plan:
    return Plan.model_validate(DEMO)


def test_free_space_follows_the_object(demo: Plan) -> None:
    """Объект переехал — свободная площадь в его прежнем месте снова свободна."""
    space = passability.free_space(demo)
    assert space is not None
    from shapely.geometry import Point

    was_here = Point(1.35, 4.3)  # стойка выдачи посылок
    assert not space.contains(was_here)
    after = passability.free_space(moved("eq_pvz", (5.0, 5.0)))
    assert after is not None
    assert after.contains(was_here), "на прежнем месте снова свободно"
    assert not after.contains(Point(5.0, 5.0)), "на новом месте стоит стойка"


def test_free_space_honours_exclude(demo: Plan) -> None:
    """Расчёт «без этого объекта» не должен приходить из кэша расчёта «со всеми»."""
    whole = passability.free_space(demo)
    without = passability.free_space(demo, exclude={"eq_pvz"})
    assert whole is not None and without is not None
    assert without.area > whole.area + 1.0


def hall(*extra: dict) -> Plan:
    """Пустой зал 12 × 8 м с одним окном обслуживания: на нём видно ширину прохода."""
    return Plan.model_validate(
        {
            "ops": {"id": "ops_hall", "name": "Зал", "bbox": {"width_m": 12.0, "height_m": 8.0}},
            "walls": [
                {"id": "h1", "from": [0, 0], "to": [12, 0], "type": "outer"},
                {"id": "h2", "from": [12, 0], "to": [12, 8], "type": "outer"},
                {"id": "h3", "from": [12, 8], "to": [0, 8], "type": "outer"},
                {"id": "h4", "from": [0, 8], "to": [0, 0], "type": "outer"},
            ],
            "doors": [{"id": "h_main", "type": "entrance", "wall": [5.0, 0, 6.0, 0], "width_m": 1.0}],
            "zones": [
                {
                    "id": "h_zone",
                    "type": "client_area",
                    "label": "Зал",
                    "polygon": [[0, 0], [12, 0], [12, 8], [0, 8]],
                }
            ],
            "equipment": [
                {
                    "id": "h_win",
                    "type": "cashier_window",
                    "label": "Окно",
                    "position": [10.0, 4.0],
                    "size_m": [1.2, 0.8],
                    "operator_position": [11.0, 4.0],
                },
                *extra,
            ],
        }
    )


def test_route_width_reacts_to_a_blocked_corridor() -> None:
    """Два стеллажа, сдвинутых к проходу, обязаны сузить маршрут, а не отдать прежний ответ."""
    before, _ = passability.route_summary(hall(), VISITOR_TARGET_TYPES)
    racks = [
        {
            "id": f"h_rack{index}",
            "type": "storage_rack",
            "label": "Стеллаж",
            "position": [7.0, y],
            "size_m": [1.0, 3.0],
        }
        for index, y in enumerate((2.1, 5.9))
    ]
    after, _ = passability.route_summary(hall(*racks), VISITOR_TARGET_TYPES)
    assert before is not None and after is not None
    assert before >= 3.0, "в пустом зале проход шире нормируемого"
    assert 0.5 < after < 1.0, f"между стеллажами остаётся 0,8 м, а получено {after}"


def test_entrance_point_follows_the_main_door(demo: Plan) -> None:
    """Главным входом назначили служебную дверь — точка начала пути переехала к ней."""
    first = passability.entrance_point(demo)
    document = json.loads(json.dumps(DEMO))
    document["flow_hints"]["main_entrance"] = "d_service"
    second = passability.entrance_point(Plan.model_validate(document))
    assert first is not None and second is not None
    assert first != second
    assert second[0] > 13.0, "служебная дверь в правой стене"


def test_turning_circle_reacts_to_a_smaller_zone(demo: Plan) -> None:
    """Зону ожидания обрезали до узкой полосы — круг разворота в неё больше не вписывается."""
    before, _ = passability.largest_clear_circle(demo, ("client_area", "waiting"))
    document = json.loads(json.dumps(DEMO))
    for zone in document["zones"]:
        if zone["type"] in ("client_area", "waiting"):
            zone["polygon"] = [[0.2, 8.4], [1.0, 8.4], [1.0, 8.8], [0.2, 8.8]]
    after, _ = passability.largest_clear_circle(Plan.model_validate(document), ("client_area", "waiting"))
    assert after < before
    assert after <= 0.8
