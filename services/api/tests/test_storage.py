"""Хранилище планов: одинаковое поведение на SQLite и на PostgreSQL.

PostgreSQL проверяется, когда задан OPS_TWIN_TEST_DATABASE_URL — адрес пустой учебной базы.
Без него те же проверки проходят на SQLite.
"""

from __future__ import annotations

import os
import threading
from pathlib import Path

import pytest

from app.storage import PlanStore, PostgresPlanStore

TEST_URL = os.environ.get("OPS_TWIN_TEST_DATABASE_URL")


@pytest.fixture(params=["sqlite", "postgres"])
def store(request: pytest.FixtureRequest, tmp_path: Path):
    if request.param == "sqlite":
        return PlanStore(tmp_path / "plans.sqlite")
    if not TEST_URL:
        pytest.skip("OPS_TWIN_TEST_DATABASE_URL не задан")
    backend = PostgresPlanStore(TEST_URL)
    for plan in backend.list_plans():
        backend.delete(plan["id"])
    return backend


DOCUMENT = {"ops": {"id": "t", "name": "Проверка"}, "walls": [], "zones": [], "equipment": [], "свои_поля": {"б": 2, "а": 1}}


def test_round_trip_keeps_document_exactly(store) -> None:
    plan_id, version = store.create("План", DOCUMENT, "первая")
    assert version == 1
    assert store.get(plan_id) == DOCUMENT
    # Порядок ключей тоже сохраняется: план открывается ровно таким, каким его сохранили.
    assert list(store.get(plan_id)["свои_поля"]) == ["б", "а"]


def test_versions_grow_and_old_ones_stay(store) -> None:
    plan_id, _ = store.create("План", DOCUMENT)
    changed = {**DOCUMENT, "ops": {"id": "t", "name": "Изменён"}}
    assert store.save_version(plan_id, changed, "правка", title="Новое имя") == 2
    assert store.get(plan_id)["ops"]["name"] == "Изменён"
    assert store.get(plan_id, 1)["ops"]["name"] == "Проверка"
    assert [v["version"] for v in store.list_versions(plan_id)] == [1, 2]
    listed = {p["id"]: p for p in store.list_plans()}
    assert listed[plan_id]["title"] == "Новое имя"
    assert listed[plan_id]["versions"] == 2


def test_missing_plan_raises_key_error(store) -> None:
    with pytest.raises(KeyError):
        store.get("нет-такого")
    with pytest.raises(KeyError):
        store.save_version("нет-такого", DOCUMENT)
    with pytest.raises(KeyError):
        store.delete("нет-такого")


def test_delete_removes_versions(store) -> None:
    plan_id, _ = store.create("План", DOCUMENT)
    store.save_version(plan_id, DOCUMENT)
    store.delete(plan_id)
    with pytest.raises(KeyError):
        store.list_versions(plan_id)


def test_parallel_saves_get_distinct_versions(store) -> None:
    """Одновременные сохранения выстраиваются в очередь, а не спорят за номер версии."""
    plan_id, _ = store.create("План", DOCUMENT)
    errors: list[BaseException] = []

    def save() -> None:
        try:
            store.save_version(plan_id, DOCUMENT)
        except BaseException as error:  # noqa: BLE001 — собираем, чтобы показать в проверке
            errors.append(error)

    threads = [threading.Thread(target=save) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert not errors
    assert [v["version"] for v in store.list_versions(plan_id)] == list(range(1, 10))


def test_records_are_upserted_and_scoped(store) -> None:
    store.put_record("incident", "a", {"n": 1}, "plan-1")
    store.put_record("incident", "b", {"n": 2}, "plan-2")
    store.put_record("incident", "a", {"n": 3}, "plan-1")
    assert store.get_record("incident", "a") == {"n": 3}
    assert store.list_records("incident", "plan-1") == [{"n": 3}]
    assert len(store.list_records("incident")) == 2
    store.delete_record("incident", "a")
    assert store.get_record("incident", "a") is None
