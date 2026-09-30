"""Сменный слой разбора фразы языковой моделью.

Проверяется главное свойство: сервис не зависит от модели. Без настроек слой выключен и в сеть
не выходит; с настройками — разбирает ответ модели, но не верит ему на слово: типов, зон и
объектов, которых нет в плане, в действиях не появится. Любая осечка модели возвращает разбор
словарю, а не роняет запрос.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.assistant import llm, recommend
from app.models import Plan

DEMO = Path(__file__).resolve().parents[3] / "data" / "demo_plan.json"
CONFIG = llm.Settings(url="http://модель.local/v1/chat/completions", key="k", model="тест-1", timeout_s=1.0)


@pytest.fixture(scope="module")
def plan() -> Plan:
    return Plan.model_validate(json.loads(DEMO.read_text(encoding="utf-8")))


def reply(payload: dict | str) -> str:
    return payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False)


def answering(monkeypatch: pytest.MonkeyPatch, payload: dict | str) -> list[str]:
    """Подменяет обращение к модели заранее заданным ответом. Возвращает список запросов."""
    asked: list[str] = []

    def fake(config: llm.Settings, prompt: str, text: str) -> str:
        asked.append(text)
        return reply(payload)

    monkeypatch.setattr(llm, "_ask", fake)
    return asked


def test_disabled_without_settings(plan: Plan, monkeypatch: pytest.MonkeyPatch) -> None:
    """Без переменных окружения слой молчит и в сеть не ходит."""
    monkeypatch.delenv("OPS_TWIN_LLM_URL", raising=False)
    monkeypatch.delenv("OPS_TWIN_LLM_MODEL", raising=False)

    def forbidden(*_args: object, **_kwargs: object) -> str:
        raise AssertionError("без настроек запрос к модели недопустим")

    monkeypatch.setattr(llm, "_ask", forbidden)
    assert llm.understand("поставь кассу", plan) is None


def test_answer_becomes_actions(plan: Plan, monkeypatch: pytest.MonkeyPatch) -> None:
    answering(
        monkeypatch,
        {
            "actions": [
                {"kind": "add", "equipment_type": "waiting_seat", "zone_id": "z_waiting", "count": 3},
                {"kind": "question", "question": "area"},
            ],
            "unknown": ["про погоду"],
        },
    )
    understanding = llm.understand("народу много, добавь сидений и скажи площадь", plan, CONFIG)
    assert understanding is not None
    assert understanding.source == "языковая модель тест-1"
    assert [a.kind for a in understanding.actions] == ["add", "question"]
    assert understanding.actions[0].count == 3
    assert understanding.actions[0].zone_id == "z_waiting"
    assert understanding.unknown == ["про погоду"]


def test_invented_equipment_is_dropped(plan: Plan, monkeypatch: pytest.MonkeyPatch) -> None:
    """Модель придумала тип, которого нет в справочнике: действие не должно появиться."""
    answering(monkeypatch, {"actions": [{"kind": "add", "equipment_type": "кофемашина"}]})
    assert llm.understand("поставь кофемашину", plan, CONFIG) is None


def test_invented_zone_is_ignored_but_action_stays(plan: Plan, monkeypatch: pytest.MonkeyPatch) -> None:
    answering(
        monkeypatch,
        {"actions": [{"kind": "add", "equipment_type": "atm", "zone_id": "z_нет_такой"}]},
    )
    understanding = llm.understand("поставь банкомат", plan, CONFIG)
    assert understanding is not None
    assert understanding.actions[0].zone_id is None


def test_fenced_json_is_understood(plan: Plan, monkeypatch: pytest.MonkeyPatch) -> None:
    """Модели любят обрамлять ответ пояснением и оградкой — это не должно мешать."""
    answering(
        monkeypatch,
        'Конечно! ```json\n{"actions": [{"kind": "fix"}]}\n``` Готово.',
    )
    understanding = llm.understand("разберись с нарушениями", plan, CONFIG)
    assert understanding is not None and understanding.actions[0].kind == "fix"


def test_broken_answer_falls_back_to_dictionary(plan: Plan, monkeypatch: pytest.MonkeyPatch) -> None:
    answering(monkeypatch, "модель сегодня не в настроении")
    assert llm.understand("убери банкомат", plan, CONFIG) is None


def test_network_failure_falls_back_to_dictionary(plan: Plan, monkeypatch: pytest.MonkeyPatch) -> None:
    def broken(config: llm.Settings, prompt: str, text: str) -> str:
        raise OSError("сеть недоступна")

    monkeypatch.setattr(llm, "_ask", broken)
    monkeypatch.setenv("OPS_TWIN_LLM_URL", CONFIG.url)
    monkeypatch.setenv("OPS_TWIN_LLM_MODEL", CONFIG.model)
    result = recommend.run_command(plan, "убери банкомат")
    assert result.source == "разбор фразы", "разбор должен вернуться словарю"
    assert result.understood, "команда обязана выполниться и без модели"
