"""Проверки HTTP-слоя и хранилища.

Главная из них — повторное открытие: сохранённый план должен возвращаться без потерь,
включая зоны, оборудование, идентификаторы и атрибуты.
"""

from __future__ import annotations

import importlib
import json
from pathlib import Path
from typing import Iterator

import pytest
from fastapi.testclient import TestClient

SAMPLE = Path(__file__).resolve().parents[3] / "data" / "example_plan.json"


@pytest.fixture()
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    monkeypatch.setenv("OPS_TWIN_DB", str(tmp_path / "plans.sqlite"))
    monkeypatch.setenv("OPS_TWIN_SAMPLE", str(SAMPLE))
    import app.main as main

    importlib.reload(main)
    with TestClient(main.app) as test_client:
        yield test_client


@pytest.fixture(scope="module")
def document() -> dict:
    return json.loads(SAMPLE.read_text(encoding="utf-8"))


def test_health(client: TestClient) -> None:
    assert client.get("/health").json() == {"status": "ok"}


def test_rules_always_cite_a_source(client: TestClient) -> None:
    rules = client.get("/api/rules").json()
    assert rules
    for rule in rules:
        assert rule["source"], rule["id"]
        assert rule["clause"], rule["id"]


def test_unknown_rule_returns_404(client: TestClient) -> None:
    assert client.get("/api/rules/нет-такого").status_code == 404


def test_sample_plan_is_served(client: TestClient) -> None:
    body = client.get("/api/sample-plan").json()
    assert body["ops"]["id"]


def test_plan_round_trip_preserves_document(client: TestClient, document: dict) -> None:
    created = client.post(
        "/api/plans", json={"title": "ОПС-1", "document": document, "note": "исходный"}
    )
    assert created.status_code == 201
    plan_id = created.json()["id"]

    reopened = client.get(f"/api/plans/{plan_id}")
    assert reopened.status_code == 200
    assert reopened.json() == document


def test_versions_are_kept_separately(client: TestClient, document: dict) -> None:
    plan_id = client.post("/api/plans", json={"title": "ОПС-1", "document": document}).json()["id"]

    changed = json.loads(json.dumps(document))
    changed["equipment"][0]["position"] = [1.0, 1.0]
    saved = client.put(
        f"/api/plans/{plan_id}", json={"document": changed, "note": "передвинули кассу"}
    )
    assert saved.json()["version"] == 2

    first = client.get(f"/api/plans/{plan_id}?version=1").json()
    second = client.get(f"/api/plans/{plan_id}?version=2").json()
    assert first["equipment"][0]["position"] != second["equipment"][0]["position"]

    versions = client.get(f"/api/plans/{plan_id}/versions").json()
    assert [v["version"] for v in versions] == [1, 2]
    assert versions[1]["note"] == "передвинули кассу"


def test_missing_plan_reports_404(client: TestClient) -> None:
    response = client.get("/api/plans/нет-такого-плана")
    assert response.status_code == 404
    assert "не найден" in response.json()["detail"]


def test_broken_document_explains_what_is_wrong(client: TestClient) -> None:
    """Пользователь, загрузивший неверный файл, должен увидеть причину, а не код ошибки."""
    response = client.post(
        "/api/plans",
        json={"title": "битый", "document": {"zones": [{"id": "z", "type": "client_area"}]}},
    )
    assert response.status_code == 422
    detail = response.json()["detail"]
    assert "не соответствует формату" in detail["сообщение"]
    assert detail["что проверить"]
    assert all({"поле", "проблема"} <= set(item) for item in detail["что проверить"])


def test_delete_removes_plan(client: TestClient, document: dict) -> None:
    plan_id = client.post("/api/plans", json={"title": "ОПС", "document": document}).json()["id"]
    assert client.delete(f"/api/plans/{plan_id}").status_code == 204
    assert client.get(f"/api/plans/{plan_id}").status_code == 404


def test_validate_endpoint_returns_findings(client: TestClient, document: dict) -> None:
    report = client.post("/api/validate", json={"document": document}).json()
    assert report["errors"] >= 1
    assert any(f["rule_id"] == "no_overlaps" for f in report["findings"])
    assert all(f["source"] and f["clause"] for f in report["findings"])


def test_validate_respects_rule_overrides(client: TestClient, document: dict) -> None:
    enabled = client.post("/api/validate", json={"document": document}).json()
    disabled = client.post(
        "/api/validate",
        json={"document": document, "rule_overrides": {"passage_between_workplaces": False}},
    ).json()
    assert any(f["rule_id"] == "passage_between_workplaces" for f in enabled["findings"])
    assert not any(f["rule_id"] == "passage_between_workplaces" for f in disabled["findings"])


def test_validate_stored_plan(client: TestClient, document: dict) -> None:
    plan_id = client.post("/api/plans", json={"title": "ОПС", "document": document}).json()["id"]
    report = client.get(f"/api/plans/{plan_id}/validate").json()
    assert report["errors"] >= 1


def test_metrics_report_areas_and_counts(client: TestClient, document: dict) -> None:
    metrics = client.post("/api/metrics", json={"document": document}).json()
    assert metrics["площадь_общая_м2"] == pytest.approx(78.0, rel=0.01)
    assert metrics["оборудование_всего"] == 12
    assert metrics["оборудование_по_типам"]["cashier_window"] == 2
    assert set(metrics["площади_зон_м2"]) == {"z_client", "z_back", "z_waiting", "z_queue"}


def test_check_ids_endpoint(client: TestClient, document: dict) -> None:
    body = client.post("/api/plans/check-ids", json={"document": document}).json()
    assert body["уникальны"] is True

    duplicated = json.loads(json.dumps(document))
    duplicated["equipment"][1]["id"] = duplicated["equipment"][0]["id"]
    body = client.post("/api/plans/check-ids", json={"document": duplicated}).json()
    assert body["уникальны"] is False


def test_dictionaries_merge_plan_types(client: TestClient, document: dict) -> None:
    custom = json.loads(json.dumps(document))
    custom["dictionaries"] = {"equipment_types": [{"code": "coffee_machine", "label": "Кофемашина"}]}
    body = client.post("/api/dictionaries", json={"document": custom}).json()
    codes = {t["code"] for t in body["equipment_types"]}
    assert {"cashier_window", "coffee_machine"} <= codes


def test_capacity_endpoint(client: TestClient, document: dict) -> None:
    body = client.post("/api/capacity", json={"document": document}).json()
    assert body["groups"]


def test_assistant_place_rejects_unknown_type(client: TestClient, document: dict) -> None:
    response = client.post(
        "/api/assistant/place", json={"document": document, "equipment_type": "нет_такого"}
    )
    assert response.status_code == 422
    assert "отсутствует в справочнике" in response.json()["detail"]


def test_assistant_place_returns_explained_recommendation(client: TestClient, document: dict) -> None:
    body = client.post(
        "/api/assistant/place", json={"document": document, "equipment_type": "storage_rack"}
    ).json()
    assert body["reasons"]
    assert body["effects"]


def test_saving_updates_plan_title(client: TestClient, document: dict) -> None:
    created = client.post("/api/plans", json={"title": "Старое имя", "document": document}).json()
    client.put(
        f"/api/plans/{created['id']}",
        json={"document": document, "note": "переименование", "title": "Новое имя"},
    )
    titles = {p["id"]: p["title"] for p in client.get("/api/plans").json()}
    assert titles[created["id"]] == "Новое имя"


def test_parallel_saves_do_not_break_the_store(client: TestClient, document: dict) -> None:
    """Одновременные сохранения выстраиваются в очередь: версии идут подряд, без потерь."""
    import threading

    created = client.post("/api/plans", json={"title": "Параллельный", "document": document}).json()
    results: list[int] = []
    errors: list[str] = []

    def save() -> None:
        try:
            response = client.put(f"/api/plans/{created['id']}", json={"document": document})
            results.append(response.json()["version"])
        except Exception as error:  # pragma: no cover - падение видно в списке
            errors.append(str(error))

    threads = [threading.Thread(target=save) for _ in range(5)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert not errors
    assert sorted(results) == [2, 3, 4, 5, 6]


def test_heavy_requests_answer_when_the_service_is_busy(client: TestClient, document: dict) -> None:
    """Пока занят единственный слот расчёта, сервис отвечает понятным отказом, а не молчит."""
    from app import concurrency

    concurrency._slots.acquire()
    original = concurrency.WAIT_TIMEOUT_S
    concurrency.WAIT_TIMEOUT_S = 0.1
    try:
        response = client.post("/api/assistant/improve", json={"document": document})
    finally:
        concurrency.WAIT_TIMEOUT_S = original
        concurrency._slots.release()

    assert response.status_code == 503
    assert "занят" in response.json()["detail"]
