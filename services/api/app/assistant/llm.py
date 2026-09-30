"""Разбор фразы языковой моделью — сменный слой над словарным разбором.

Зачем он нужен: словарный разбор (`language.py`) знает область и работает без интернета, но
не понимает вольных формулировок вроде «народу много стоит у входа, придумай что-нибудь».
Языковая модель понимает, зато требует ключа, сети и денег, и может ошибиться или выдумать.

Поэтому модель здесь — только переводчик фразы в набор действий. Нормы, геометрию, выбор места
и последствия по-прежнему считает сервис: модель не может ни поставить объект мимо норматива, ни
выдумать тип оборудования — всё, чего нет в справочнике плана, отбрасывается при разборе ответа.

Подключение — переменными окружения, без ключа слой выключен и ничего не делает:

    OPS_TWIN_LLM_URL    адрес совместимого с OpenAI /chat/completions
    OPS_TWIN_LLM_KEY    ключ (Bearer)
    OPS_TWIN_LLM_MODEL  название модели
    OPS_TWIN_LLM_TIMEOUT  сколько ждать ответа, секунды (по умолчанию 8)

Любая осечка — нет ключа, таймаут, не тот JSON, незнакомый тип оборудования — тихо возвращает
None, и сервис разбирает фразу словарём. Ответ на команду всегда говорит, чем она разобрана.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from dataclasses import dataclass

from app.assistant.language import Action, Understanding
from app.dictionaries import equipment_types
from app.models import Plan

KINDS = ("add", "move", "remove", "fix", "check", "question")
QUESTIONS = ("count", "where", "area", "state")
MAX_ACTIONS = 8
MAX_COUNT = 20

PROMPT = """Ты переводишь фразу планировщика отделения почты в набор действий. Отвечай только
JSON без пояснений, по схеме:
{"actions": [{"kind": "add|move|remove|fix|check|question", "equipment_type": "код или null",
"object_id": "идентификатор объекта или null", "zone_id": "идентификатор зоны или null",
"count": 1, "near_entrance": false, "near_wall": false, "near_partition": false,
"question": "count|where|area|state или null"}], "unknown": ["часть фразы без ответа"]}

Правила:
- kind=add — поставить новое, move — переставить существующее, remove — убрать,
  fix — устранить нарушения, check — проверить план, question — вопрос о плане, ничего не менять.
- equipment_type только из перечня типов ниже, zone_id и object_id только из перечней плана.
- Мест и координат не придумывай: их подбирает сервис.
- Если часть фразы не про план — положи её в unknown.

Типы оборудования: {types}
Зоны плана: {zones}
Объекты плана: {objects}"""


@dataclass(frozen=True)
class Settings:
    url: str
    key: str
    model: str
    timeout_s: float

    @property
    def enabled(self) -> bool:
        return bool(self.url and self.model)


def settings() -> Settings:
    try:
        timeout = float(os.environ.get("OPS_TWIN_LLM_TIMEOUT", 8.0))
    except ValueError:
        timeout = 8.0
    return Settings(
        url=os.environ.get("OPS_TWIN_LLM_URL", "").strip(),
        key=os.environ.get("OPS_TWIN_LLM_KEY", "").strip(),
        model=os.environ.get("OPS_TWIN_LLM_MODEL", "").strip(),
        timeout_s=max(1.0, timeout),
    )


def _catalogue(plan: Plan) -> tuple[str, str, str]:
    types = ", ".join(f"{code} ({definition.label})" for code, definition in equipment_types(plan).items())
    zones = ", ".join(f"{zone.id} ({zone.label or zone.type})" for zone in plan.zones) or "нет"
    objects = ", ".join(f"{item.id} ({item.label or item.type})" for item in plan.equipment[:60]) or "нет"
    return types, zones, objects


def _ask(config: Settings, prompt: str, text: str) -> str:
    """Запрос к модели. Возвращает текст ответа; ошибки поднимаются наружу."""
    body = json.dumps(
        {
            "model": config.model,
            "temperature": 0,
            "messages": [
                {"role": "system", "content": prompt},
                {"role": "user", "content": text},
            ],
        },
        ensure_ascii=False,
    ).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    if config.key:
        headers["Authorization"] = f"Bearer {config.key}"
    request = urllib.request.Request(config.url, data=body, headers=headers, method="POST")
    with urllib.request.urlopen(request, timeout=config.timeout_s) as answer:  # noqa: S310 — адрес задаёт хозяин сервиса
        payload = json.loads(answer.read().decode("utf-8"))
    return str(payload["choices"][0]["message"]["content"])


def _json_block(text: str) -> dict:
    """JSON из ответа модели: она любит обрамлять его пояснениями и оградкой из кавычек."""
    cleaned = text.strip()
    if "```" in cleaned:
        parts = cleaned.split("```")
        cleaned = max(parts, key=len).removeprefix("json").strip()
    start, end = cleaned.find("{"), cleaned.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("в ответе модели нет JSON")
    parsed = json.loads(cleaned[start : end + 1])
    if not isinstance(parsed, dict):
        raise ValueError("ответ модели не объект")
    return parsed


def _action(raw: object, plan: Plan) -> Action | None:
    """Одно действие из ответа модели. Всё, чего нет в плане и справочнике, отбрасывается."""
    if not isinstance(raw, dict):
        return None
    kind = str(raw.get("kind") or "").strip()
    if kind not in KINDS:
        return None
    known_types = equipment_types(plan)
    type_code = raw.get("equipment_type")
    type_code = str(type_code) if type_code else None
    if type_code is not None and type_code not in known_types:
        return None
    zone_id = raw.get("zone_id")
    zone_id = str(zone_id) if zone_id else None
    if zone_id is not None and zone_id not in {zone.id for zone in plan.zones}:
        zone_id = None
    object_id = raw.get("object_id")
    object_id = str(object_id) if object_id else None
    if object_id is not None and object_id not in {item.id for item in plan.equipment}:
        return None
    question = raw.get("question")
    question = str(question) if question else None
    if kind == "question" and question not in QUESTIONS:
        question = "state"
    if kind in ("add", "move", "remove") and type_code is None and object_id is None:
        return None
    try:
        count = int(raw.get("count") or 1)
    except (TypeError, ValueError):
        count = 1
    return Action(
        kind=kind,
        equipment_type=type_code,
        object_id=object_id,
        zone_id=zone_id,
        count=max(1, min(MAX_COUNT, count)),
        near_entrance=bool(raw.get("near_entrance")),
        near_partition=bool(raw.get("near_partition")),
        near_wall=bool(raw.get("near_wall")),
        question=question if kind == "question" else None,
        text=str(raw.get("text") or ""),
    )


def understand(text: str, plan: Plan, config: Settings | None = None) -> Understanding | None:
    """Разбор фразы моделью. None означает «слой выключен или не справился» — тогда словарь."""
    config = config or settings()
    if not config.enabled or not text.strip():
        return None
    types, zones, objects = _catalogue(plan)
    prompt = PROMPT.replace("{types}", types).replace("{zones}", zones).replace("{objects}", objects)
    try:
        answer = _json_block(_ask(config, prompt, text))
    except (urllib.error.URLError, OSError, ValueError, KeyError, IndexError, TypeError):
        # Сервис обязан работать и без модели: разбор просто ложится на словарь.
        return None
    raw_actions = answer.get("actions")
    if not isinstance(raw_actions, list):
        return None
    result = Understanding(source=f"языковая модель {config.model}")
    for raw in raw_actions[:MAX_ACTIONS]:
        action = _action(raw, plan)
        if action is None:
            if isinstance(raw, dict) and raw.get("text"):
                result.unknown.append(str(raw["text"]))
            continue
        result.actions.append(action)
    unknown = answer.get("unknown")
    if isinstance(unknown, list):
        result.unknown.extend(str(part) for part in unknown[:MAX_ACTIONS] if str(part).strip())
    return result if result.actions else None
