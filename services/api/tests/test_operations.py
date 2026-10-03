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
    # Подпись ставит сервер по сеансу — в поле автора, а не в тексте примечания от клиента.
    assert any("Морозова" in (v.get("author") or "") for v in versions)
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


def test_guest_cannot_forge_inspection_dates_but_can_edit_layout(client: TestClient, plan_id: str) -> None:
    document = client.get(f"/api/plans/{plan_id}").json()
    moved = json.loads(json.dumps(document))
    moved["equipment"][0]["position"] = [moved["equipment"][0]["position"][0] + 0.1, moved["equipment"][0]["position"][1]]
    assert client.put(f"/api/plans/{plan_id}", json={"document": moved, "note": "сдвиг"}).status_code == 200
    forged = json.loads(json.dumps(moved))
    forged["equipment"][0].setdefault("inventory", {})["last_inspection"] = "2026-10-01"
    forged["equipment"][0]["inventory"]["next_inspection"] = "2099-01-01"
    assert client.put(f"/api/plans/{plan_id}", json={"document": forged, "note": "Осмотр выполнен — Морозова"}).status_code == 403
    client.post("/api/auth/demo", json={"role": "engineer"})
    assert client.put(f"/api/plans/{plan_id}", json={"document": forged, "note": "осмотр"}).status_code == 200
    versions = client.get(f"/api/plans/{plan_id}/versions").json()
    assert versions[-1]["author"].startswith("Ковалёв") and versions[-2]["author"] == ""


def test_deleting_a_plan_removes_its_incidents(client: TestClient, plan_id: str) -> None:
    import app.main as main

    client.post(f"/api/plans/{plan_id}/incidents", json={"object_id": window_id(client, plan_id), "text": "Сломан"})
    assert main.store.list_records("incident", plan_id)
    client.post("/api/auth/demo", json={"role": "head"})
    assert client.delete(f"/api/plans/{plan_id}").status_code == 204
    assert main.store.list_records("incident", plan_id) == []


def test_empty_incident_text_is_rejected(client: TestClient, plan_id: str) -> None:
    response = client.post(f"/api/plans/{plan_id}/incidents", json={"object_id": window_id(client, plan_id), "text": "   "})
    assert response.status_code == 422


def test_repeating_the_same_status_does_not_grow_history(client: TestClient, plan_id: str) -> None:
    incident = client.post(
        f"/api/plans/{plan_id}/incidents", json={"object_id": window_id(client, plan_id), "text": "Нет связи"}
    ).json()
    client.post("/api/auth/demo", json={"role": "engineer"})
    url = f"/api/plans/{plan_id}/incidents/{incident['id']}"
    client.patch(url, json={"status": "in_progress"})
    again = client.patch(url, json={"status": "in_progress"}).json()
    assert len(again["history"]) == 2


def test_today_counts_problems_with_correct_wording(client: TestClient, plan_id: str) -> None:
    actions = client.get(f"/api/plans/{plan_id}/today", params={"day": "2026-10-03"}).json()["actions"]
    check = next(a for a in actions if a["kind"] == "check")
    assert "откройте «Обзор»" in check["text"] and "нарушений" not in check["text"]
    assert operations.plural(21, "проблема", "проблемы", "проблем") == "проблема"
    assert operations.plural(12, "проблема", "проблемы", "проблем") == "проблем"


def test_today_uses_the_day_the_employee_lives_in(client: TestClient, plan_id: str) -> None:
    assert client.get(f"/api/plans/{plan_id}/today", params={"day": "2026-10-03"}).json()["date"] == "2026-10-03"
    assert client.get(f"/api/plans/{plan_id}/today").json()["date"] == operations.local_today().isoformat()


def test_lockout_is_per_address(client: TestClient) -> None:
    """Пять неверных паролей с одного адреса не запирают учётку для владельца с другого адреса."""
    import app.main as main

    for _ in range(5):
        try:
            main.accounts.login("inzhener", "wrong", "203.0.113.7")
        except PermissionError:
            pass
    with pytest.raises(RuntimeError):
        main.accounts.login("inzhener", "test-only-password", "203.0.113.7")
    assert main.accounts.login("inzhener", "test-only-password", "198.51.100.2").user.role == "engineer"


def test_demo_accounts_follow_the_settings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from app.accounts import Accounts
    from app.storage import PlanStore

    store = PlanStore(tmp_path / "accounts.sqlite")
    monkeypatch.setenv("OPS_TWIN_DEMO_PASSWORD", "first-password")
    Accounts(store).ensure_demo_users()
    monkeypatch.setenv("OPS_TWIN_DEMO_PASSWORD", "second-password")
    accounts = Accounts(store)
    accounts.ensure_demo_users()
    with pytest.raises(PermissionError):
        accounts.login("nachalnik", "first-password")
    assert accounts.login("nachalnik", "second-password").user.role == "head"
    monkeypatch.setenv("OPS_TWIN_DEMO_USERS", "0")
    accounts = Accounts(store)
    accounts.ensure_demo_users()
    with pytest.raises(PermissionError):
        accounts.login("nachalnik", "second-password")


def test_concurrent_demo_network_is_added_once(client: TestClient) -> None:
    from concurrent.futures import ThreadPoolExecutor

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: client.post("/api/network/demo").json()["added"], range(2)))
    assert sorted(results) == [0, 8]
