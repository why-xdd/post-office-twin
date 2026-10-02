"""Вход сотрудников, обращения о неисправностях, осмотры и сводка «Сегодня»."""

from __future__ import annotations

import importlib
import json
from collections.abc import Iterator
from datetime import date
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import operations

DEMO = Path(__file__).resolve().parents[3] / "data" / "demo_plan.json"


@pytest.fixture()
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    monkeypatch.setenv("OPS_TWIN_DB", str(tmp_path / "plans.sqlite"))
    monkeypatch.setenv("OPS_TWIN_DEMO_PASSWORD", "test-only-password")
    import app.main as main

    importlib.reload(main)
    with TestClient(main.app) as test_client:
        yield test_client


@pytest.fixture()
def plan_id(client: TestClient) -> str:
    document = json.loads(DEMO.read_text(encoding="utf-8"))
    return client.post("/api/plans", json={"title": "Заречное", "document": document}).json()["id"]


def window_id(client: TestClient, plan_id: str) -> str:
    document = client.get(f"/api/plans/{plan_id}").json()
    return next(e["id"] for e in document["equipment"] if e["type"] == "cashier_window")


def test_guest_mode_is_unchanged(client: TestClient) -> None:
    me = client.get("/api/auth/me").json()
    assert me["user"] is None and me["demo"] is True
    assert len(me["roles"]) == 4


def test_login_sets_http_only_cookie_and_logout_clears_it(client: TestClient) -> None:
    response = client.post("/api/auth/login", json={"login": "nachalnik", "password": "test-only-password"})
    assert response.status_code == 200
    assert "httponly" in response.headers["set-cookie"].lower()
    assert client.get("/api/auth/me").json()["user"]["role"] == "head"
    client.post("/api/auth/logout")
    assert client.get("/api/auth/me").json()["user"] is None


def test_wrong_password_is_refused_and_brute_force_is_stopped(client: TestClient) -> None:
    codes = [
        client.post("/api/auth/login", json={"login": "inzhener", "password": "wrong"}).status_code for _ in range(6)
    ]
    assert codes[:5] == [401] * 5 and codes[5] == 429


def test_password_is_not_stored_in_clear(client: TestClient) -> None:
    import app.main as main

    record = main.store.get_record("user", "nachalnik")
    assert record and "test-only-password" not in json.dumps(record)


def test_anyone_reports_only_staff_manage(client: TestClient, plan_id: str) -> None:
    target = window_id(client, plan_id)
    created = client.post(
        f"/api/plans/{plan_id}/incidents",
        json={"object_id": target, "text": "Не печатает чеки", "out_of_service": True, "reporter": "Оля"},
    )
    assert created.status_code == 201
    incident = created.json()
    assert incident["status"] == "new" and "без входа" in incident["created_by"]
    url = f"/api/plans/{plan_id}/incidents/{incident['id']}"
    assert client.patch(url, json={"status": "in_progress"}).status_code == 401
    client.post("/api/auth/demo", json={"role": "operator"})
    assert client.patch(url, json={"status": "in_progress"}).status_code == 403
    client.post("/api/auth/demo", json={"role": "engineer"})
    done = client.patch(url, json={"status": "resolved", "note": "Заменён принтер"}).json()
    assert done["status"] == "resolved" and len(done["history"]) == 2


def test_unknown_object_is_rejected(client: TestClient, plan_id: str) -> None:
    response = client.post(f"/api/plans/{plan_id}/incidents", json={"object_id": "nope", "text": "x"})
    assert response.status_code == 404


def test_broken_window_lowers_working_windows_today(client: TestClient, plan_id: str) -> None:
    before = client.get(f"/api/plans/{plan_id}/today").json()
    working = sum(w["working"] for w in before["windows"])
    target = window_id(client, plan_id)
    client.post(f"/api/plans/{plan_id}/incidents", json={"object_id": target, "text": "Сломан", "out_of_service": True})
    after = client.get(f"/api/plans/{plan_id}/today").json()
    assert sum(w["working"] for w in after["windows"]) == working - 1
    assert after["out_of_service"] and any(a["kind"] == "incident" for a in after["actions"])


def test_assistant_answers_what_is_broken_from_incidents(client: TestClient, plan_id: str) -> None:
    target = window_id(client, plan_id)
    client.post(f"/api/plans/{plan_id}/incidents", json={"object_id": target, "text": "Нет связи", "out_of_service": True})
    document = client.get(f"/api/plans/{plan_id}").json()
    result = client.post(
        "/api/assistant/command", json={"document": document, "text": "что сломано?", "plan_id": plan_id}
    ).json()
    assert "Нет связи" in json.dumps(result, ensure_ascii=False)


def test_inspection_done_needs_role_and_saves_version(client: TestClient, plan_id: str) -> None:
    document = client.get(f"/api/plans/{plan_id}").json()
    item = document["equipment"][0]["id"]
    url = f"/api/plans/{plan_id}/inspections"
    assert client.post(url, json={"object_id": item, "next_inspection": "2099-01-01"}).status_code == 401
    client.post("/api/auth/demo", json={"role": "head"})
    saved = client.post(url, json={"object_id": item, "next_inspection": "2099-01-01"})
    assert saved.status_code == 200
    versions = client.get(f"/api/plans/{plan_id}/versions").json()
    assert any("Морозова" in (v.get("note") or "") for v in versions)
    updated = client.get(f"/api/plans/{plan_id}").json()
    inventory = next(e for e in updated["equipment"] if e["id"] == item)["inventory"]
    assert inventory["next_inspection"] == "2099-01-01"


def test_inspection_keeps_previous_interval_or_asks() -> None:
    document = {"equipment": [{"id": "a", "inventory": {"last_inspection": "2026-01-01", "next_inspection": "2026-04-01"}}]}
    _, following = operations.mark_inspected(document, operations.InspectionDone(object_id="a"), date(2026, 5, 1))
    assert following == "2026-07-30"
    bare = {"equipment": [{"id": "b"}]}
    with pytest.raises(ValueError):
        operations.mark_inspected(bare, operations.InspectionDone(object_id="b"), date(2026, 5, 1))
