"""Экран «Сеть»: очередь отделений на ремонт с прозрачным баллом."""

from __future__ import annotations

import importlib
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    monkeypatch.setenv("OPS_TWIN_DB", str(tmp_path / "plans.sqlite"))
    import app.main as main

    importlib.reload(main)
    with TestClient(main.app) as test_client:
        yield test_client


def test_demo_network_loads_once(client: TestClient) -> None:
    assert client.post("/api/network/demo").json()["added"] == 8
    assert client.post("/api/network/demo").json()["added"] == 0


def test_ranking_is_explained_and_ordered(client: TestClient) -> None:
    client.post("/api/network/demo")
    report = client.get("/api/network").json()
    offices = report["offices"]
    assert [o["rank"] for o in offices] == list(range(1, 9))
    scores = [o["score"] for o in offices]
    assert scores == sorted(scores, reverse=True)
    for office in offices:
        assert office["score"] == pytest.approx(sum(f["points"] for f in office["factors"]), abs=0.11)
    assert {w["key"] for w in report["weights"]} >= {"fire", "accessibility", "windows"}


def test_problems_land_where_expected(client: TestClient) -> None:
    client.post("/api/network/demo")
    offices = {o["title"]: o for o in client.get("/api/network").json()["offices"]}
    lesnoe = offices["Отделение «Лесное» (условное)"]
    assert any(f["key"] == "fire" for f in lesnoe["factors"])
    vokzalnoe = offices["Отделение «Вокзальное» (условное)"]
    assert any(f["key"] == "windows" for f in vokzalnoe["factors"])
    polevoe = offices["Отделение «Полевое» (условное)"]
    assert polevoe["rank"] > lesnoe["rank"]


def test_broken_window_reported_by_staff_raises_priority(client: TestClient) -> None:
    client.post("/api/network/demo")
    offices = client.get("/api/network").json()["offices"]
    target = next(o for o in offices if o["title"] == "Отделение «Северное» (условное)")
    client.post(
        f"/api/plans/{target['plan_id']}/incidents",
        json={"object_id": "eq_win_1", "text": "Не включается", "out_of_service": True},
    )
    after = next(o for o in client.get("/api/network").json()["offices"] if o["plan_id"] == target["plan_id"])
    assert after["score"] > target["score"]
