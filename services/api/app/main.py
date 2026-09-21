"""HTTP-интерфейс сервиса цифрового плана отделения."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import time
import uuid

from fastapi import FastAPI, File, Form, HTTPException, Request, Response, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from pydantic import BaseModel, Field, ValidationError

from app.assistant import capacity as capacity_model
from app.cache import assistant_cache, fingerprint, validation_cache
from app.concurrency import heavy_slot
from app.assistant import conclusion, recommend
from app.assistant.capacity import CapacityReport
from app.assistant.recommend import AssistantReport, ImprovementReport, Recommendation
from app.dictionaries import equipment_types, zone_types
from app.geometry import core
from app.importer import build as importer
from app.models import Plan, duplicate_ids
from app.rules.catalog import DEFAULT_RULES, Rule, rules_by_id
from app.rules.engine import ValidationReport, validate
from app.storage import open_store

DB_PATH = os.environ.get("OPS_TWIN_DB", "storage/plans.sqlite")
def _sample_plan_path() -> Path:
    """Где лежит пример плана.

    В контейнере он лежит в /app/samples, при запуске из исходников — в data/ в корне проекта.
    Переменная OPS_TWIN_SAMPLE задаёт путь явно.
    """
    named = os.environ.get("OPS_TWIN_SAMPLE")
    if named:
        return Path(named)
    packaged = Path("/app/samples/demo_plan.json")
    if packaged.exists():
        return packaged
    return Path(__file__).resolve().parents[3] / "data" / "demo_plan.json"


SAMPLE_PLAN = _sample_plan_path()

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
# Планы и отчёты — текстовый JSON, он сжимается в разы: на медленной сети это заметно.
app.add_middleware(GZipMiddleware, minimum_size=1024)

MAX_BODY_BYTES = int(os.environ.get("OPS_TWIN_MAX_BODY", 8 * 1024 * 1024))


@app.middleware("http")
async def trace_and_limit(request: Request, call_next):
    """Отсекает слишком большие тела и помечает каждый ответ идентификатором и временем.

    Идентификатор запроса попадает и в ответ, и в журнал: по нему разбирают жалобу «у меня
    не сохранилось» без догадок. Время ответа в Server-Timing видно прямо в инструментах
    разработчика браузера.
    """
    size = request.headers.get("content-length")
    if size is not None and size.isdigit() and int(size) > MAX_BODY_BYTES:
        return Response(
            status_code=413,
            content=json.dumps(
                {"detail": f"Файл плана больше {MAX_BODY_BYTES // (1024 * 1024)} МБ — такой план не принимается."},
                ensure_ascii=False,
            ),
            media_type="application/json",
        )
    request_id = request.headers.get("x-request-id") or uuid.uuid4().hex[:12]
    started = time.perf_counter()
    response = await call_next(request)
    elapsed_ms = (time.perf_counter() - started) * 1000
    response.headers["X-Request-Id"] = request_id
    response.headers["Server-Timing"] = f"total;dur={elapsed_ms:.1f}"
    return response


store = open_store(os.environ.get("DATABASE_URL"), DB_PATH)


class CreatePlanRequest(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    document: dict[str, Any]
    note: str = ""


class SaveVersionRequest(BaseModel):
    document: dict[str, Any]
    note: str = ""
    title: str | None = Field(
        default=None, description="Новое название плана в списке; пусто — оставить прежнее"
    )


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


def _with_slot(what: str, compute):
    with heavy_slot(what):
        return compute()


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
        version = store.save_version(plan_id, payload.document, payload.note, payload.title)
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
    # Один и тот же документ проверяется одинаково: повторный запрос берётся из памяти.
    key = fingerprint("validate", payload.document, payload.rule_overrides)
    return validation_cache.get_or_compute(key, lambda: validate(plan, payload.rule_overrides or None))


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
    key = fingerprint("advise", payload.document)
    return assistant_cache.get_or_compute(
        key,
        lambda: _with_slot("подбор рекомендаций", lambda: recommend.advise(_parse_plan(payload.document))),
    )


@app.post(
    "/api/assistant/improve",
    response_model=ImprovementReport,
    summary="Пошаговое улучшение плана",
)
def assistant_improve(payload: SaveVersionRequest, fixes_only: bool = False) -> ImprovementReport:
    key = fingerprint("improve", payload.document, fixes_only)
    return assistant_cache.get_or_compute(
        key,
        lambda: _with_slot(
            "подбор расстановки",
            lambda: recommend.improve(_parse_plan(payload.document), fixes_only=fixes_only),
        ),
    )


class CommandRequest(BaseModel):
    document: dict[str, Any]
    text: str = Field(min_length=1, max_length=500, description="Команда словами, например «поставь две кассы у перегородки»")


@app.post("/api/assistant/command", response_model=recommend.CommandResult, summary="Команда словами")
def assistant_command(payload: CommandRequest) -> recommend.CommandResult:
    """Разбирает фразу и готовит рекомендации: план меняется только после согласия человека."""
    plan = _parse_plan(payload.document)
    key = fingerprint("command", payload.document, payload.text)
    return assistant_cache.get_or_compute(
        key, lambda: _with_slot("разбор команды", lambda: recommend.run_command(plan, payload.text))
    )


@app.post("/api/assistant/conclusion", response_model=conclusion.Conclusion, summary="Заключение по плану")
def assistant_conclusion(payload: SaveVersionRequest) -> conclusion.Conclusion:
    """Текст для сдачи: что проверено, что нарушено, что предлагается и каким будет эффект."""
    plan = _parse_plan(payload.document)
    key = fingerprint("conclusion", payload.document)
    return assistant_cache.get_or_compute(
        key, lambda: _with_slot("подготовку заключения", lambda: conclusion.build(plan))
    )


@app.post("/api/import/drawing", response_model=importer.ImportResult, summary="Импорт чертежа")
def import_drawing(
    file: UploadFile = File(description="Картинка чертежа или PDF"),
    real_width_m: float = Form(gt=0.5, le=200, description="Действительная ширина помещения в метрах"),
    name: str = Form(default="План с чертежа", max_length=200),
    page: int = Form(default=0, ge=0, le=50, description="Страница PDF, считая с нуля"),
) -> importer.ImportResult:
    """Распознаёт чертёж и возвращает черновик плана: стены, проёмы, перегородки.

    Ничего не сохраняет: пользователь сначала смотрит результат, правит его в редакторе и только
    потом создаёт план.
    """
    data = file.file.read(MAX_BODY_BYTES + 1)
    if len(data) > MAX_BODY_BYTES:
        raise HTTPException(status_code=413, detail="Файл больше допустимого размера.")
    if not data:
        raise HTTPException(status_code=422, detail="Файл пустой.")
    try:
        with heavy_slot("распознавание чертежа"):
            return importer.build(data, real_width_m=real_width_m, page=page, name=name)
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from None
    except Exception:
        raise HTTPException(
            status_code=422,
            detail="Не удалось прочитать файл: нужен рисунок плана в PNG, JPEG или PDF.",
        ) from None



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
    with heavy_slot("поиск места"):
        return recommend.place_equipment(plan, payload.equipment_type, payload.zone_id)


def _web_dir() -> Path | None:
    """Собранный клиент, если он есть рядом: тогда сервис отдаёт и сайт, и API с одного адреса.

    OPS_TWIN_WEB задаёт каталог явно; иначе ищется /app/web (образ) и apps/web/dist (исходники).
    Без собранного клиента сервис работает как чистый API — так он запускается в разработке.
    """
    named = os.environ.get("OPS_TWIN_WEB")
    candidates = [Path(named)] if named else [
        Path("/app/web"),
        Path(__file__).resolve().parents[3] / "apps" / "web" / "dist",
    ]
    for candidate in candidates:
        if (candidate / "index.html").is_file():
            return candidate
    return None


WEB_DIR = _web_dir()
if WEB_DIR is not None:
    from fastapi.staticfiles import StaticFiles

    # Монтируется последним: маршруты API и /health объявлены выше и имеют приоритет.
    app.mount("/", StaticFiles(directory=WEB_DIR, html=True), name="web")
