"""Разбор команд словами.

Батарея фраз здесь — мера того, насколько сервис понимает живую речь. Каждая строка либо
проверена руками в панели ассистента, либо выросла из разбора, где сервис понял фразу неверно:
«поставь почтовый ящик О-12» ставил двенадцать ящиков, «сколько у нас окон» понималось как
«поставить окно», «добавь скамейку в зону ожидания» теряло зону.

Вторая часть не менее важна: фразы, которые понимать нельзя. Сервис обязан сказать «не понял»,
а не угадать команду из вежливости.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.assistant import language, recommend
from app.models import Plan

DEMO = Path(__file__).resolve().parents[3] / "data" / "demo_plan.json"


@pytest.fixture(scope="module")
def plan() -> Plan:
    return Plan.model_validate(json.loads(DEMO.read_text(encoding="utf-8")))


# фраза, вид действия, тип оборудования (None — не проверяем), ожидания по деталям
COMMANDS = [
    ("поставь почтовый ящик в оптимальное место", "add", "package_drop_box", {"count": 1}),
    ("добавь скамейку в зону ожидания", "add", "waiting_seat", {"zone": "z_waiting"}),
    ("нужно ещё одно окно обслуживания", "add", "cashier_window", {"count": 1}),
    ("поставь два банкомата у входа", "add", "atm", {"count": 2, "near_entrance": True}),
    ("установи терминал очереди рядом с входом", "add", "self_service_kiosk", {"near_entrance": True}),
    ("давай поставим ещё скамью", "add", "waiting_seat", {"count": 1}),
    ("добавь 3 скамьи в накопитель", "add", "waiting_seat", {"count": 3, "zone": "z_queue"}),
    ("поставь стеллаж к стене", "add", "storage_rack", {"near_wall": True}),
    ("поставь кассу в 2 метрах от перегородки", "add", "cashier_window", {"count": 1, "near_partition": True}),
    ("поставь почтовый ящик О-12", "add", "package_drop_box", {"count": 1}),
    ("паставь скамью у стены", "add", "waiting_seat", {"near_wall": True}),
    ("добавь термнал очереди", "add", "self_service_kiosk", {}),
    ("убери банкомат", "remove", "atm", {}),
    ("удали О-05", "remove", None, {"object": "eq_locker"}),
    ("убрать лишние скамьи", "remove", "waiting_seat", {}),
    ("перенеси скамью в зону ожидания", "move", "waiting_seat", {"zone": "z_waiting"}),
    ("переставь банкомат к стене", "move", "atm", {"near_wall": True}),
    ("перенеси О-03 к перегородке", "move", None, {"object": "eq_win_3", "near_partition": True}),
    ("передвинь табло в зону ожидания", "move", "info_display", {"zone": "z_waiting"}),
    ("проверь план", "check", None, {}),
    ("что не так с планом", "check", None, {}),
    ("исправь всё", "fix", None, {}),
    ("оптимизируй расстановку", "fix", None, {}),
    ("сделай проход шире", "fix", None, {}),
    ("расширь проход к окнам", "fix", None, {}),
    ("приведи план в порядок", "fix", None, {}),
    ("сколько у нас окон обслуживания?", "question", "cashier_window", {"asked": "count"}),
    ("где стоит банкомат?", "question", "atm", {"asked": "where"}),
    ("сколько скамеек в зоне ожидания?", "question", "waiting_seat", {"asked": "count", "zone": "z_waiting"}),
    ("какая свободная площадь?", "question", None, {"asked": "area"}),
    ("покажи камеру зала", "question", "camera", {"asked": "where"}),
]

# Фразы, которые командой не являются: сервис должен переспросить, а не угадывать.
NOT_COMMANDS = [
    "привет",
    "не надо ничего менять",
    "спасибо",
    "сделай красиво",
    "напиши отчёт",
    "выгрузи опись в 1с",
    "сохрани план",
    "отмени последнее действие",
    "открой третью версию плана",
    "распечатай опись",
]


@pytest.mark.parametrize("text,kind,type_code,extra", COMMANDS)
def test_phrase_is_understood(plan: Plan, text: str, kind: str, type_code: str | None, extra: dict) -> None:
    result = language.understand(text, plan)
    assert result.actions, f"фраза «{text}» осталась непонятой"
    action = result.actions[0]
    assert action.kind == kind
    if type_code is not None:
        assert action.equipment_type == type_code
    if "count" in extra:
        assert action.count == extra["count"]
    if "zone" in extra:
        assert action.zone_id == extra["zone"]
    if "object" in extra:
        assert action.object_id == extra["object"]
    if "asked" in extra:
        assert action.question == extra["asked"]
    for flag in ("near_entrance", "near_wall", "near_partition"):
        if extra.get(flag):
            assert getattr(action, flag) is True, f"во фразе «{text}» не отмечено {flag}"


@pytest.mark.parametrize("text", NOT_COMMANDS)
def test_phrase_is_not_invented(plan: Plan, text: str) -> None:
    result = language.understand(text, plan)
    assert not result.actions, f"фраза «{text}» понята как {[a.kind for a in result.actions]}"
    assert result.unknown, "непонятая фраза обязана вернуться списком, а не пропасть"


def test_question_is_answered_without_changing_the_plan(plan: Plan) -> None:
    """На вопрос сервис отвечает по плану и ничего не предлагает менять."""
    result = recommend.run_command(plan, "сколько скамеек в зоне ожидания?")
    assert result.recommendations == []
    assert len(result.answers) == 1
    assert "2" in result.answers[0], result.answers[0]
    assert "Зона ожидания" in result.answers[0]


def test_where_answer_names_zone_and_place(plan: Plan) -> None:
    result = recommend.run_command(plan, "где стоит банкомат?")
    assert result.answers and "О-10" in result.answers[0]
    assert "зона" in result.answers[0].lower()


def test_check_answers_with_the_current_state(plan: Plan) -> None:
    result = recommend.run_command(plan, "проверь план")
    assert result.answers and "арушен" in result.answers[0]


def test_address_in_a_phrase_is_not_a_quantity(plan: Plan) -> None:
    """«Поставь почтовый ящик О-12» — один ящик, а не двенадцать."""
    result = recommend.run_command(plan, "поставь почтовый ящик О-12")
    assert len(result.recommendations) == 1
