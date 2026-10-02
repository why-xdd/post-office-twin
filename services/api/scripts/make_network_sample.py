"""Демонстрационная сеть: восемь условных отделений для экрана «Сеть».

Отделения выдуманы и построены из двух примеров (`demo_plan.json`, `example_plan.json`): у
каждого свой поток, свой набор проблем или их отсутствие. Задача — показать, как экран
расставляет очередь на ремонт, а не изобразить настоящие отделения.

    uv run python scripts/make_network_sample.py   # пишет data/network/*.json
"""

from __future__ import annotations

import copy
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.assistant import recommend  # noqa: E402
from app.geometry import core  # noqa: E402
from app.models import Plan  # noqa: E402

DATA = Path(__file__).resolve().parents[3] / "data"
OUT = DATA / "network"


def load(name: str) -> dict:
    return json.loads((DATA / name).read_text(encoding="utf-8"))


def fixed(document: dict) -> dict:
    plan = Plan.model_validate(document)
    return recommend.improve(plan, fixes_only=True).improved_plan


def named(document: dict, slug: str, name: str, visitors: int | None = None) -> dict:
    result = copy.deepcopy(document)
    result["ops"]["id"] = f"ops_net_{slug}"
    result["ops"]["name"] = f"Отделение «{name}» (условное)"
    if visitors is not None:
        result.setdefault("flow_hints", {})["avg_daily_visitors"] = visitors
    return result


def with_rack_in_vestibule(document: dict) -> dict:
    result = copy.deepcopy(document)
    plan = Plan.model_validate(result)
    vestibule = next(z for z in plan.zones if z.type == "vestibule")
    centre = core.zone_polygon(vestibule).centroid
    result["equipment"].append(
        {"id": "eq_rack_vest", "type": "storage_rack", "label": "Стеллаж с коробками", "position": [round(centre.x, 2), round(centre.y, 2)], "size_m": [1.0, 0.4]}
    )
    return result


def without(document: dict, object_id: str) -> dict:
    result = copy.deepcopy(document)
    result["equipment"] = [e for e in result["equipment"] if e["id"] != object_id]
    return result


def main() -> None:
    demo, example = load("demo_plan.json"), load("example_plan.json")
    demo_fixed, example_fixed = fixed(demo), fixed(example)
    offices = {
        "zarechnoe": named(demo, "zarechnoe", "Заречное"),
        "severnoe": named(demo_fixed, "severnoe", "Северное", 380),
        "vokzalnoe": named(demo_fixed, "vokzalnoe", "Вокзальное", 720),
        "lesnoe": named(with_rack_in_vestibule(demo_fixed), "lesnoe", "Лесное", 300),
        "sadovoe": named(without(demo, "eq_win_3"), "sadovoe", "Садовое", 560),
        "rechnoe": named(example, "rechnoe", "Речное"),
        "polevoe": named(example_fixed, "polevoe", "Полевое", 160),
        "shkolnoe": named(example_fixed, "shkolnoe", "Школьное", 340),
    }
    OUT.mkdir(exist_ok=True)
    for slug, document in offices.items():
        Plan.model_validate(document)
        (OUT / f"{slug}.json").write_text(json.dumps(document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(slug, document["ops"]["name"])


if __name__ == "__main__":
    main()
