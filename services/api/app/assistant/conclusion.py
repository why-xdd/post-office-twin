"""Заключение по плану — текст для сдачи заказчику.

Сначала собираются факты: что за отделение, что нашла проверка, что предлагает ассистент и
каким будет эффект. По этим фактам строится текст по шаблону, поэтому для одного и того же
плана он всегда одинаков, а каждое число в нём можно проверить.
"""

from __future__ import annotations

import re
from typing import Any

from pydantic import BaseModel, Field

from app.assistant import objective, recommend
from app.geometry import core
from app.models import Plan
from app.rules.engine import Severity, validate


class Conclusion(BaseModel):
    text: str = Field(description="Готовый текст заключения")
    facts: dict[str, Any] = Field(description="Числа, на которых построен текст")


def _human(text: str) -> str:
    """Человеческий вид: без внутренних идентификаторов и с запятой в числах."""
    without_ids = re.sub(r"\s*\([A-Za-z][A-Za-z0-9_.-]*\)", "", text)
    return re.sub(r"(\d)\.(\d)", r"\1,\2", without_ids)


def _plural(count: int, one: str, few: str, many: str) -> str:
    tail = abs(count) % 100
    if 11 <= tail <= 14:
        return many
    tail %= 10
    if tail == 1:
        return one
    if 2 <= tail <= 4:
        return few
    return many


def facts(plan: Plan, fixes_only: bool = True) -> dict[str, Any]:
    """Всё, что нужно для заключения: состояние плана, находки и эффект предложенных изменений."""
    report = validate(plan)
    before = objective.score(plan, errors=report.errors, warnings=report.warnings)
    improvement = recommend.improve(plan, fixes_only=fixes_only)
    improved = Plan.model_validate(improvement.improved_plan)
    after = objective.score(improved, plan)
    return {
        "отделение": plan.ops.name or "без названия",
        "площадь_м2": core.total_area(plan),
        "объектов": len(plan.equipment),
        "зон": len(plan.zones),
        "проверено_правил": len(report.checked_rules),
        "нарушений": report.errors,
        "предупреждений": report.warnings,
        "нарушения": [
            {
                "правило": finding.title,
                "текст": finding.message,
                "документ": finding.source,
                "пункт": finding.clause,
            }
            for finding in report.findings
            if finding.severity is Severity.ERROR
        ],
        "предложено_шагов": len(improvement.steps),
        # Почему после исправления что-то осталось: например, окна кассы движок не двигает.
        "пометки": improvement.notes,
        "шаги": [step.recommendation.title for step in improvement.steps],
        "после_исправления": {
            "нарушений": after.errors,
            "предупреждений": after.warnings,
            "проход_от_входа_м": after.entrance_width_m,
            "узкое_место_м": after.narrowest_route_m,
            "путь_до_окон_м": after.walk_m,
            "мест_ожидания": after.waiting_capacity,
        },
        "до_исправления": {
            "проход_от_входа_м": before.entrance_width_m,
            "узкое_место_м": before.narrowest_route_m,
            "путь_до_окон_м": before.walk_m,
            "мест_ожидания": before.waiting_capacity,
        },
    }


def _num(value: float) -> str:
    """Число для текста: до двух знаков, запятая, без хвостовых нулей."""
    return f"{value:.2f}".rstrip("0").rstrip(".").replace(".", ",")


def from_template(data: dict[str, Any]) -> str:
    """Заключение по шаблону: то же самое, что сказал бы инженер, глядя на числа."""
    errors = int(data["нарушений"])
    after = data["после_исправления"]
    before = data["до_исправления"]
    lines: list[str] = []
    # Название часто уже начинается словом «Отделение»: тогда второй раз его не пишем.
    name = str(data["отделение"])
    title = name if name.lower().startswith(("отделение", "опс", "почтовое")) else f"Отделение «{name}»"
    lines.append(
        f"{title}: площадь {data['площадь_м2']} м², "
        f"{data['объектов']} {_plural(data['объектов'], 'объект', 'объекта', 'объектов')} "
        f"в {data['зон']} {_plural(data['зон'], 'зоне', 'зонах', 'зонах')}. "
        f"Планировка проверена по {data['проверено_правил']} "
        f"{_plural(data['проверено_правил'], 'правилу', 'правилам', 'правилам')} и на целостность данных."
    )
    if errors:
        names = ", ".join(sorted({item["правило"].lower() for item in data["нарушения"]}))
        lines.append(
            f"Найдено {errors} {_plural(errors, 'нарушение', 'нарушения', 'нарушений')}: {names}. "
            f"Предупреждений: {data['предупреждений']}."
        )
    else:
        lines.append("Нарушений норм не найдено.")
    if data["предложено_шагов"]:
        lines.append(
            f"Ассистент предлагает {data['предложено_шагов']} "
            f"{_plural(data['предложено_шагов'], 'изменение', 'изменения', 'изменений')}: "
            + "; ".join(step.lower() for step in data["шаги"])
            + "."
        )
        gains: list[str] = []

        def shift(key: str, label: str, unit: str = "") -> None:
            # Показатель попадает в текст, только если он изменился: «2,04 → 2,04 м» ничего не говорит.
            was, now = before.get(key), after.get(key)
            if was is None or now is None or round(was, 2) == round(now, 2):
                return
            gains.append(f"{label} {_num(was)} → {_num(now)}{unit}")

        shift("узкое_место_м", "узкое место на пути к окнам", " м")
        shift("проход_от_входа_м", "свободный путь от входа", " м")
        shift("путь_до_окон_м", "путь посетителя до окон", " м")
        shift("мест_ожидания", "мест ожидания")
        lines.append(
            f"После изменений нарушений {after['нарушений']}, предупреждений {after['предупреждений']}"
            + ("; " + ", ".join(gains) if gains else "")
            + "."
        )
    lines.extend(data.get("пометки", []))
    return _human(" ".join(lines))


def build(plan: Plan) -> Conclusion:
    """Заключение по плану: факты и текст по ним."""
    data = facts(plan)
    return Conclusion(text=from_template(data), facts=data)
