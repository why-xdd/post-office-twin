"""Заключение по плану — текст для сдачи заказчику.

Сначала собираются факты: что за отделение, что нашла проверка, что предлагает ассистент и
каким будет эффект. По этим фактам всегда строится текст по шаблону — он не зависит ни от
какой модели и всегда одинаков для одного и того же плана. Если подключена языковая модель,
она пересказывает те же факты связно; числа ей передаются готовыми, придумывать нечего.
"""

from __future__ import annotations

import re
from typing import Any

from pydantic import BaseModel, Field

from app.assistant import llm, objective, recommend
from app.geometry import core
from app.models import Plan
from app.rules.engine import Severity, validate


class Conclusion(BaseModel):
    text: str = Field(description="Готовый текст заключения")
    source: str = Field(description="Чем составлен текст: шаблон или языковая модель")
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
        "шаги": [step.recommendation.title for step in improvement.steps],
        "после_исправления": {
            "нарушений": after.errors,
            "предупреждений": after.warnings,
            "проход_от_входа_м": after.entrance_width_m,
            "путь_до_окон_м": after.walk_m,
            "мест_ожидания": after.waiting_capacity,
        },
        "до_исправления": {
            "проход_от_входа_м": before.entrance_width_m,
            "путь_до_окон_м": before.walk_m,
            "мест_ожидания": before.waiting_capacity,
        },
    }


def from_template(data: dict[str, Any]) -> str:
    """Заключение по шаблону: то же самое, что сказал бы инженер, глядя на числа."""
    errors = int(data["нарушений"])
    after = data["после_исправления"]
    before = data["до_исправления"]
    lines: list[str] = []
    lines.append(
        f"Отделение «{data['отделение']}»: площадь {data['площадь_м2']} м², "
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
        if before.get("проход_от_входа_м") is not None and after.get("проход_от_входа_м") is not None:
            gains.append(f"проход от входа {before['проход_от_входа_м']} → {after['проход_от_входа_м']} м")
        if before.get("путь_до_окон_м") is not None and after.get("путь_до_окон_м") is not None:
            gains.append(f"путь посетителя до окон {before['путь_до_окон_м']} → {after['путь_до_окон_м']} м")
        if before.get("мест_ожидания") is not None and after.get("мест_ожидания") is not None:
            gains.append(f"мест ожидания {before['мест_ожидания']} → {after['мест_ожидания']}")
        lines.append(
            f"После изменений нарушений {after['нарушений']}, предупреждений {after['предупреждений']}"
            + ("; " + ", ".join(gains) if gains else "")
            + "."
        )
    return _human(" ".join(lines))


def build(plan: Plan, use_model: bool = True) -> Conclusion:
    """Заключение: шаблон всегда, пересказ моделью — если она подключена."""
    data = facts(plan)
    text = from_template(data)
    if use_model:
        written = llm.conclusion(data)
        if written:
            return Conclusion(text=written, source="языковая модель по посчитанным числам", facts=data)
    return Conclusion(text=text, source="шаблон по посчитанным числам", facts=data)
