"""Архив демонстрационного проекта: исходный план, изменённый, рекомендации и их эффект.

Всё считается заново при сборке, теми же функциями, что работают в сервисе: цифры в описании
совпадают с тем, что покажет редактор на этих файлах.

Запуск из services/api:
    uv run python scripts/build_demo_package.py
"""

from __future__ import annotations

import argparse
import json
import zipfile
from datetime import date
from pathlib import Path
from typing import Any

import re

from app.assistant import conclusion, recommend
from app.models import Plan
from app.rules.engine import Severity, validate

ROOT = Path(__file__).resolve().parents[3]
DEMO = ROOT / "data" / "demo_plan.json"

LABELS = {
    "errors": ("Ошибок проверки", ""),
    "warnings": ("Предупреждений", ""),
    "entrance_route_width": ("Ширина пути от входа", "м"),
    "min_route_width": ("Самое узкое место на пути посетителя", "м"),
    "walk_m": ("Средний путь посетителя до окон", "м"),
    "waiting_capacity": ("Мест в зоне ожидания", ""),
    "free_area": ("Свободная площадь пола", "м²"),
    "occupied_area": ("Занято оборудованием", "м²"),
    "equipment_total": ("Объектов на плане", ""),
}


def _number(value: Any) -> str:
    if value is None:
        return "—"
    if isinstance(value, float):
        return f"{value:.2f}".rstrip("0").rstrip(".").replace(".", ",")
    return str(value)


def _human(text: str, names: dict[str, str]) -> str:
    """Текст для человека: названия объектов вместо идентификаторов, запятая в дробях."""
    text = re.sub(r"«([A-Za-z][A-Za-z0-9_]*)»", lambda m: f"«{names.get(m.group(1), m.group(1))}»", text)
    text = re.sub(r"\s*\(([A-Za-z][A-Za-z0-9_]*)\)", lambda m: "" if m.group(1) in names else m.group(0), text)
    return re.sub(r"(?<=\d)\.(?=\d)", ",", text)


def _findings_block(plan: Plan) -> list[str]:
    report = validate(plan, {})
    lines = [f"Ошибок: {report.errors}, предупреждений: {report.warnings}.", ""]
    for finding in report.findings:
        if finding.severity is Severity.INFO:
            continue
        kind = "Ошибка" if finding.severity is Severity.ERROR else "Предупреждение"
        source = f" ({finding.source}, {finding.clause})" if finding.clause else ""
        lines.append(f"- {kind}: {_human(finding.message, _names(plan))}{source}")
    return lines


def _names(plan: Plan) -> dict[str, str]:
    return {item.id: item.label or item.id for item in plan.equipment}


def _recommendation_block(index: int, rec: recommend.Recommendation, names: dict[str, str]) -> list[str]:
    lines = [f"### {index}. {_human(rec.title, names)}", "", _human(rec.summary, names), ""]
    if rec.changes:
        lines.append("Что изменить:")
        for change in rec.changes:
            where = f" → ({_number(change.position[0])}; {_number(change.position[1])})" if change.position else ""
            before = (
                f" с ({_number(change.previous_position[0])}; {_number(change.previous_position[1])})"
                if change.previous_position
                else ""
            )
            verb = {"move": "переставить", "add": "добавить", "remove": "убрать"}.get(change.op, change.op)
            lines.append(f"- {verb} «{change.label or change.object_id}»{before}{where}, идентификатор `{change.object_id}`")
        lines.append("")
    if rec.reasons:
        lines.append("Почему:")
        for reason in rec.reasons:
            source = f" — {reason.source}" + (f", {reason.clause}" if reason.clause else "") if reason.source else ""
            lines.append(f"- {_human(reason.text, names)}{source}")
        lines.append("")
    if rec.assumptions:
        lines.append("Допущения:")
        lines.extend(f"- {text}" for text in rec.assumptions)
        lines.append("")
    if rec.effects:
        lines.append("| Показатель | Было | Станет |")
        lines.append("|---|---|---|")
        for effect in rec.effects:
            unit = f" {effect.unit}" if effect.unit else ""
            lines.append(f"| {effect.label} | {_number(effect.before)}{unit} | {_number(effect.after)}{unit} |")
        lines.append("")
    return lines


def build(out: Path) -> dict[str, Any]:
    original_doc = json.loads(DEMO.read_text(encoding="utf-8"))
    original = Plan.model_validate(original_doc)

    # Шаг 1: ассистент снимает ошибки — перестановки по одной, пока план улучшается.
    improvement = recommend.improve(original)
    fixed = Plan.model_validate(improvement.improved_plan)
    # Шаг 2: остальные рекомендации к исправленному плану — объект не в своей зоне и
    # недостающее по перечню оборудование.
    advice = recommend.advise(fixed)
    extra = [r for r in advice.recommendations if r.id.startswith("zone:") or r.scenario == "missing"]
    final = fixed
    for rec in extra:
        final = recommend.apply_changes(final, rec.changes)
    final_doc = final.model_dump(by_alias=True, exclude_none=True)
    final_doc["ops"]["name"] = original.ops.name + " — после рекомендаций"

    applied = [step.recommendation for step in improvement.steps] + extra
    before_report, after_report = validate(original, {}), validate(final, {})
    initial = improvement.initial
    after_summary = recommend.summarize(final)

    lines = [
        f"# {original.ops.name}: рекомендации ассистента и их эффект",
        "",
        f"Собрано {date.today().strftime('%d.%m.%Y')} по планам из этого архива.",
        "",
        "## Что в архиве",
        "",
        "| Файл | Что это |",
        "|---|---|",
        "| `plan_original.json` | Исходный план отделения |",
        "| `plan_after.json` | План после всех рекомендаций ассистента |",
        "| `recommendations.json` | Рекомендации ассистента в машинном виде: изменения, причины, эффект |",
        "| `conclusion.txt` | Заключение по исходному плану для согласования |",
        "| `README.md` | Это описание |",
        "",
        "## Итог",
        "",
        "| Показатель | Исходный план | После рекомендаций |",
        "|---|---|---|",
    ]
    for key, (label, unit) in LABELS.items():
        was = initial.get(key)
        now = after_summary.get(key)
        if key == "errors":
            was, now = before_report.errors, after_report.errors
        if key == "warnings":
            was, now = before_report.warnings, after_report.warnings
        suffix = f" {unit}" if unit else ""
        lines.append(f"| {label} | {_number(was)}{suffix} | {_number(now)}{suffix} |")
    lines += [
        "",
        "## Что нашла проверка в исходном плане",
        "",
        *_findings_block(original),
        "",
        "## Рекомендации ассистента",
        "",
        "Каждая рекомендация показывается на плане и применяется в редакторе кнопкой «Применить»; "
        "ниже — те же рекомендации в том порядке, в каком они применены к исходному плану.",
        "",
    ]
    names = {**_names(original), **_names(final)}
    for index, rec in enumerate(applied, 1):
        lines += _recommendation_block(index, rec, names)
    lines += [
        "## Что осталось после рекомендаций",
        "",
        *_findings_block(final),
        "",
        "## Как открыть планы повторно",
        "",
        "В сервисе:",
        "",
        "1. Открыть https://plan-ops.onrender.com (адрес также указан в README.md репозитория).",
        "2. На первом экране нажать «Загрузить свой план» и выбрать `plan_original.json` или `plan_after.json`.",
        "3. План откроется в редакторе с зонами, оборудованием, идентификаторами, атрибутами и паспортами "
        "объектов. «Сохранить версию» записывает его в хранилище; сохранённые планы открываются из списка на "
        "первом экране, прежние версии — из меню версий у названия плана.",
        "",
        "Через API:",
        "",
        "```bash",
        "curl -X POST https://plan-ops.onrender.com/api/plans -H 'Content-Type: application/json' \\",
        "  -d \"{\\\"title\\\": \\\"Заречное\\\", \\\"document\\\": $(cat plan_original.json)}\"",
        "curl https://plan-ops.onrender.com/api/plans/ИДЕНТИФИКАТОР",
        "```",
        "",
        "Исходный план — это тот же файл, что открывается кнопкой «Открыть пример» (`data/demo_plan.json` "
        "в репозитории). Пересобрать архив: `uv run python scripts/build_demo_package.py` в `services/api`.",
        "",
    ]

    recommendations = {
        "исходный_план": {"ошибок": before_report.errors, "предупреждений": before_report.warnings},
        "после_рекомендаций": {"ошибок": after_report.errors, "предупреждений": after_report.warnings},
        "рекомендации": [rec.model_dump(exclude_none=True) for rec in applied],
        "показатели_до": initial,
        "показатели_после": after_summary,
    }
    text = conclusion.build(original).text

    out.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(out, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("README.md", "\n".join(lines))
        archive.writestr("plan_original.json", json.dumps(original_doc, ensure_ascii=False, indent=2))
        archive.writestr("plan_after.json", json.dumps(final_doc, ensure_ascii=False, indent=2))
        archive.writestr("recommendations.json", json.dumps(recommendations, ensure_ascii=False, indent=2, default=str))
        archive.writestr("conclusion.txt", text)
    return {
        "архив": str(out),
        "ошибок": f"{before_report.errors} → {after_report.errors}",
        "предупреждений": f"{before_report.warnings} → {after_report.warnings}",
        "рекомендаций": len(applied),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Архив демонстрационного проекта")
    parser.add_argument("--out", default=str(ROOT / "build" / "demo-zarechnoe.zip"))
    args = parser.parse_args()
    print(build(Path(args.out)))


if __name__ == "__main__":
    main()
