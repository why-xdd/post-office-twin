"""HTTP-интерфейс сервиса цифрового плана отделения."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Response
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field, ValidationError

from app.assistant import capacity as capacity_model
from app.assistant import recommend
from app.assistant.capacity import CapacityReport
from app.assistant.recommend import AssistantReport, ImprovementReport, Recommendation
from app.dictionaries import equipment_types, zone_types
from app.geometry import core
from app.models import Plan, duplicate_ids
from app.rules.catalog import DEFAULT_RULES, Rule, rules_by_id
from app.rules.engine import ValidationReport, validate
from app.storage import PlanStore

DB_PATH = os.environ.get("OPS_TWIN_DB", "storage/plans.sqlite")
SAMPLE_PLAN = Path(os.environ.get("OPS_TWIN_SAMPLE", "/app/samples/example_plan.json"))

app = FastAPI(
    title="Цифровой двойник отделения Почты России",
    description="Хранение и проверка цифровых планов отделения.",
    version="0.1.0",
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

store = PlanStore(DB_PATH)


class CreatePlanRequest(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    document: dict[str, Any]
    note: str = ""


class SaveVersionRequest(BaseModel):
    document: dict[str, Any]
    note: str = ""


class PlanRef(BaseModel):
    id: str
    version: int


def _parse_plan(document: dict[str, Any]) -> Plan:
    """Разбирает план и отдаёт понятную ошибку вместо стандартного отказа валидатора."""
    try:
        return Plan.model_validate(document)
    except ValidationError as exc:
        problems = [
            {
                "поле": ".".join(str(p) for p in err["loc"]),
                "проблема": err["msg"],
            }
            for err in exc.errors()[:20]
        ]
        raise HTTPException(
            status_code=422,
            detail={
                "сообщение": "Файл плана не соответствует формату и не может быть загружен.",
                "что проверить": problems,
            },
        ) from exc


@app.get("/health", summary="Проверка состояния сервиса")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/api/rules", response_model=list[Rule], summary="Реестр правил проверки")
def get_rules() -> list[Rule]:
    """Правила с указанием документа и пункта — основание каждого вывода проверки."""
    return DEFAULT_RULES


@app.get("/api/rules/{rule_id}", response_model=Rule, summary="Правило по идентификатору")
def get_rule(rule_id: str) -> Rule:
    rule = rules_by_id().get(rule_id)
    if rule is None:
        raise HTTPException(status_code=404, detail=f"Правило {rule_id} не найдено")
    return rule


@app.get("/api/plans", summary="Список планов")
def list_plans() -> list[dict[str, Any]]:
    return store.list_plans()


@app.post("/api/plans", response_model=PlanRef, status_code=201, summary="Загрузить план")
def create_plan(payload: CreatePlanRequest) -> PlanRef:
    _parse_plan(payload.document)
    plan_id, version = store.create(payload.title, payload.document, payload.note)
    return PlanRef(id=plan_id, version=version)


@app.get("/api/plans/{plan_id}", summary="Открыть план")
def get_plan(plan_id: str, version: int | None = None) -> dict[str, Any]:
    try:
        return store.get(plan_id, version)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"План {plan_id} не найден") from None


@app.put("/api/plans/{plan_id}", response_model=PlanRef, summary="Сохранить новую версию плана")
def save_plan(plan_id: str, payload: SaveVersionRequest) -> PlanRef:
    _parse_plan(payload.document)
    try:
        version = store.save_version(plan_id, payload.document, payload.note)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"План {plan_id} не найден") from None
    return PlanRef(id=plan_id, version=version)


@app.get("/api/plans/{plan_id}/versions", summary="Версии плана")
def plan_versions(plan_id: str) -> list[dict[str, Any]]:
    try:
        return store.list_versions(plan_id)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"План {plan_id} не найден") from None


@app.delete("/api/plans/{plan_id}", status_code=204, summary="Удалить план")
def delete_plan(plan_id: str) -> Response:
    try:
        store.delete(plan_id)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"План {plan_id} не найден") from None
    return Response(status_code=204)


@app.get("/api/sample-plan", summary="Пример плана в формате заказчика")
def sample_plan() -> dict[str, Any]:
    if not SAMPLE_PLAN.exists():
        raise HTTPException(status_code=404, detail="Пример плана не найден в поставке")
    return json.loads(SAMPLE_PLAN.read_text(encoding="utf-8"))


@app.post("/api/plans/check-ids", summary="Проверка уникальности идентификаторов")
def check_ids(payload: SaveVersionRequest) -> dict[str, Any]:
    plan = _parse_plan(payload.document)
    dupes = duplicate_ids(plan)
    return {
        "уникальны": not dupes,
        "повторяющиеся": dupes,
    }


class ValidateRequest(BaseModel):
    document: dict[str, Any]
    rule_overrides: dict[str, bool] = Field(
        default_factory=dict,
        description="Отключение или включение отдельных правил: идентификатор правила -> флаг",
    )


@app.post("/api/validate", response_model=ValidationReport, summary="Проверить план")
def validate_document(payload: ValidateRequest) -> ValidationReport:
    """Проверка планировки по нормативам и целостности данных.

    Каждая находка содержит документ и пункт, на которых основан вывод.
    """
    plan = _parse_plan(payload.document)
    return validate(plan, payload.rule_overrides or None)


@app.get(
    "/api/plans/{plan_id}/validate",
    response_model=ValidationReport,
    summary="Проверить сохранённый план",
)
def validate_stored(plan_id: str, version: int | None = None) -> ValidationReport:
    try:
        document = store.get(plan_id, version)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"План {plan_id} не найден") from None
    return validate(_parse_plan(document))


@app.post("/api/metrics", summary="Показатели плана")
def plan_metrics(payload: SaveVersionRequest) -> dict[str, Any]:
    """Площади и состав оборудования: основа инвентаризации и сравнения версий."""
    plan = _parse_plan(payload.document)
    by_type: dict[str, int] = {}
    by_zone: dict[str, int] = {}
    for item in plan.equipment:
        by_type[item.type] = by_type.get(item.type, 0) + 1
        key = item.zone or "вне зон"
        by_zone[key] = by_zone.get(key, 0) + 1
    return {
        "площадь_общая_м2": core.total_area(plan),
        "площадь_занятая_м2": core.occupied_area(plan),
        "площадь_свободная_м2": core.free_area(plan),
        "площади_зон_м2": {zone.id: core.area_of_zone(zone) for zone in plan.zones},
        "оборудование_всего": len(plan.equipment),
        "оборудование_по_типам": by_type,
        "оборудование_по_зонам": by_zone,
    }


class PlaceRequest(BaseModel):
    document: dict[str, Any]
    equipment_type: str = Field(min_length=1, description="Код типа оборудования из справочника")
    zone_id: str | None = Field(default=None, description="Ограничить поиск зоной")


@app.post("/api/dictionaries", summary="Справочники зон и типов оборудования")
def get_dictionaries(payload: SaveVersionRequest | None = None) -> dict[str, Any]:
    """Встроенные справочники, дополненные и переопределённые справочником из плана."""
    plan = _parse_plan(payload.document) if payload is not None else None
    return {
        "zone_types": [z.model_dump() for z in zone_types(plan).values()],
        "equipment_types": [t.model_dump() for t in equipment_types(plan).values()],
    }


@app.post("/api/capacity", response_model=CapacityReport, summary="Пропускная способность в час пик")
def plan_capacity(payload: SaveVersionRequest) -> CapacityReport:
    return capacity_model.analyze(_parse_plan(payload.document))


@app.post("/api/assistant", response_model=AssistantReport, summary="Рекомендации ассистента")
def assistant_advise(payload: SaveVersionRequest) -> AssistantReport:
    """Все рекомендации для текущего плана: исправления, пропускная способность, комплектность.

    Каждая содержит изменения для редактора, причины со ссылкой на норму, допущения и эффект.
    """
    return recommend.advise(_parse_plan(payload.document))


@app.post(
    "/api/assistant/improve",
    response_model=ImprovementReport,
    summary="Пошаговое улучшение плана",
)
def assistant_improve(payload: SaveVersionRequest, fixes_only: bool = False) -> ImprovementReport:
    return recommend.improve(_parse_plan(payload.document), fixes_only=fixes_only)


@app.post("/api/assistant/place", response_model=Recommendation, summary="Найти место для объекта")
def assistant_place(payload: PlaceRequest) -> Recommendation:
    plan = _parse_plan(payload.document)
    if payload.equipment_type not in equipment_types(plan):
        raise HTTPException(
            status_code=422,
            detail=f"Тип оборудования «{payload.equipment_type}» отсутствует в справочнике плана.",
        )
    if payload.zone_id is not None and payload.zone_id not in {z.id for z in plan.zones}:
        raise HTTPException(status_code=422, detail=f"Зона «{payload.zone_id}» на плане не найдена.")
    return recommend.place_equipment(plan, payload.equipment_type, payload.zone_id)
