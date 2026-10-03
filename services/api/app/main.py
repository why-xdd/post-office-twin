"""HTTP-интерфейс сервиса цифрового плана отделения."""

from __future__ import annotations

import ipaddress
import json
import os
from datetime import date
from pathlib import Path
from typing import Any

import threading
import time
import uuid
from urllib.parse import quote

from fastapi import FastAPI, File, Form, HTTPException, Request, Response, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from pydantic import BaseModel, Field, ValidationError

from app.assistant import capacity as capacity_model
from app.cache import assistant_cache, fingerprint, validation_cache
from app.concurrency import heavy_slot
from app.assistant import conclusion, peak, queue_log, recommend
from app.assistant import storage as parcel_storage
from app.assistant.capacity import CapacityReport
from app.assistant.recommend import AssistantReport, ImprovementReport, Recommendation
from app import exchange
from app.dictionaries import equipment_types, zone_types
from app.geometry import core
from app.importer import build as importer
from app.models import Plan, duplicate_ids
from app.rules.catalog import DEFAULT_RULES, Rule, rules_by_id
from app.vision import counter as vision
from app.rules.engine import ValidationReport, validate
from app import compare as compare_model
from app import network, operations
from app.accounts import ROLES, SESSION_HOURS, Accounts, User, allowed, demo_enabled
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


def _network_sample_dir() -> Path:
    """Условные отделения для экрана «Сеть»: в контейнере — /app/samples/network."""
    packaged = Path("/app/samples/network")
    if packaged.exists():
        return packaged
    return Path(__file__).resolve().parents[3] / "data" / "network"

app = FastAPI(
    title="Цифровой двойник отделения Почты России",
    description="Хранение и проверка цифровых планов отделения.",
    version="0.1.0",
)
# Вход хранится в cookie, поэтому запросы с другого адреса принимаются только от своих:
# локальной разработки и адресов из OPS_TWIN_ORIGINS. В поставке интерфейс и API на одном адресе.
app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in os.environ.get("OPS_TWIN_ORIGINS", "").split(",") if o.strip()],
    allow_origin_regex=r"^https?://(localhost|127\.0\.0\.1)(:\d+)?$",
    allow_credentials=True,
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
                {"detail": f"Запрос больше {MAX_BODY_BYTES // (1024 * 1024)} МБ — такой файл не принимается."},
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
accounts = Accounts(store)
accounts.ensure_demo_users()
SESSION_COOKIE = "ops_session"


def current_user(request: Request) -> User | None:
    return accounts.user_by_token(request.cookies.get(SESSION_COOKIE))


def require(request: Request, permission: str | None = None) -> User:
    user = current_user(request)
    if user is None:
        raise HTTPException(status_code=401, detail="Войдите, чтобы выполнить это действие.")
    if permission and not allowed(user, permission):
        raise HTTPException(status_code=403, detail="Это действие доступно начальнику отделения и инженеру.")
    return user


def _client_address(request: Request) -> str:
    """Адрес посетителя для счёта неудачных входов.

    X-Forwarded-For принимается, только когда запрос пришёл от прокси с локального или частного
    адреса: так работают nginx на том же сервере и хостинг с балансировщиком. Напрямую из
    интернета заголовок подставит кто угодно, и счёт попыток по нему обходился бы подменой.
    """
    direct = request.client.host if request.client else ""
    forwarded = request.headers.get("x-forwarded-for", "")
    if forwarded:
        try:
            trusted = ipaddress.ip_address(direct).is_private or ipaddress.ip_address(direct).is_loopback
        except ValueError:
            trusted = False
        if trusted:
            return forwarded.split(",")[0].strip()
    return direct


def _set_session(request: Request, response: Response, token: str) -> None:
    secure = request.url.scheme == "https" or request.headers.get("x-forwarded-proto") == "https"
    response.set_cookie(
        SESSION_COOKIE,
        token,
        max_age=SESSION_HOURS * 3600,
        httponly=True,
        samesite="lax",
        secure=secure,
        path="/",
    )


class LoginRequest(BaseModel):
    login: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=256)


class DemoLoginRequest(BaseModel):
    role: str


@app.get("/api/auth/me", summary="Кто вошёл")
def auth_me(request: Request) -> dict[str, Any]:
    user = current_user(request)
    return {
        "user": user.model_dump() if user else None,
        "demo": demo_enabled(),
        "roles": [{"role": role, "label": label} for role, label in ROLES.items()],
    }


@app.post("/api/auth/login", summary="Вход по логину и паролю")
def auth_login(payload: LoginRequest, request: Request, response: Response) -> dict[str, Any]:
    try:
        session = accounts.login(payload.login, payload.password, _client_address(request))
    except RuntimeError as error:
        raise HTTPException(status_code=429, detail=str(error)) from error
    except PermissionError as error:
        raise HTTPException(status_code=401, detail=str(error)) from error
    _set_session(request, response, session.token)
    return {"user": session.user.model_dump()}


@app.post("/api/auth/demo", summary="Вход демо-сотрудником")
def auth_demo(payload: DemoLoginRequest, request: Request, response: Response) -> dict[str, Any]:
    try:
        session = accounts.demo_login(payload.role)
    except PermissionError as error:
        raise HTTPException(status_code=403, detail=str(error)) from error
    _set_session(request, response, session.token)
    return {"user": session.user.model_dump()}


@app.post("/api/auth/logout", status_code=204, summary="Выход")
def auth_logout(request: Request) -> Response:
    accounts.logout(request.cookies.get(SESSION_COOKIE))
    response = Response(status_code=204)
    response.delete_cookie(SESSION_COOKIE, path="/")
    return response


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


def _author(user: User | None) -> str:
    """Подпись версии: кто сохранил. Пустая — сохранено без входа."""
    return f"{user.name}, {user.role_label.lower()}" if user else ""


INSPECTION_FIELDS = ("last_inspection", "next_inspection")


def _inspection_dates(document: dict[str, Any]) -> dict[str, tuple[Any, ...]]:
    result = {}
    for item in document.get("equipment") or []:
        inventory = item.get("inventory") or {}
        result[str(item.get("id"))] = tuple(inventory.get(field) for field in INSPECTION_FIELDS)
    return result


def _changes_inspections(before: dict[str, Any], after: dict[str, Any]) -> bool:
    """Меняет ли новая версия даты осмотров: правит существующие или вносит их новому объекту."""
    old = _inspection_dates(before)
    for object_id, dates in _inspection_dates(after).items():
        if dates != old.get(object_id, (None, None)) and any(dates):
            return True
    return False


@app.post("/api/plans", response_model=PlanRef, status_code=201, summary="Загрузить план")
def create_plan(payload: CreatePlanRequest, request: Request) -> PlanRef:
    _parse_plan(payload.document)
    plan_id, version = store.create(payload.title, payload.document, payload.note, _author(current_user(request)))
    return PlanRef(id=plan_id, version=version)


def _plan_exists(plan_id: str) -> bool:
    """Есть ли такой план вообще: отличает «нет плана» от «нет такой версии»."""
    try:
        return bool(store.list_versions(plan_id))
    except KeyError:
        return False


@app.get("/api/plans/{plan_id}", summary="Открыть план")
def get_plan(plan_id: str, version: int | None = None) -> dict[str, Any]:
    try:
        return store.get(plan_id, version)
    except KeyError:
        # План может быть на месте, а вот запрошенной версии не существовать: об этом и говорим,
        # иначе человек ищет пропавший план, которого никто не терял.
        if version is not None and _plan_exists(plan_id):
            raise HTTPException(
                status_code=404, detail=f"У плана {plan_id} нет версии {version}"
            ) from None
        raise HTTPException(status_code=404, detail=f"План {plan_id} не найден") from None


@app.put("/api/plans/{plan_id}", response_model=PlanRef, summary="Сохранить новую версию плана")
def save_plan(plan_id: str, payload: SaveVersionRequest, request: Request) -> PlanRef:
    """Планировку правят и без входа. Даты осмотров — нет: это отметка о работе конкретного
    человека, и её ставят начальник отделения или инженер под своим именем."""
    _parse_plan(payload.document)
    user = current_user(request)
    try:
        previous = store.get(plan_id)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"План {plan_id} не найден") from None
    if _changes_inspections(previous, payload.document) and not allowed(user, "inspection.done"):
        raise HTTPException(
            status_code=403,
            detail="Даты осмотров меняют начальник отделения и инженер: войдите под своей учётной записью.",
        )
    try:
        version = store.save_version(plan_id, payload.document, payload.note, payload.title, _author(user))
    except KeyError:
        raise HTTPException(status_code=404, detail=f"План {plan_id} не найден") from None
    return PlanRef(id=plan_id, version=version)


def _latest(plan_id: str) -> tuple[dict[str, Any], Plan]:
    try:
        document = store.get(plan_id)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"План {plan_id} не найден") from None
    return document, _parse_plan(document)


@app.get("/api/plans/{plan_id}/incidents", response_model=list[operations.Incident], summary="Обращения по плану")
def plan_incidents(plan_id: str) -> list[operations.Incident]:
    _latest(plan_id)
    return operations.list_incidents(store, plan_id)


@app.post(
    "/api/plans/{plan_id}/incidents",
    response_model=operations.Incident,
    status_code=201,
    summary="Сообщить о неисправности",
)
def report_incident(plan_id: str, payload: operations.IncidentIn, request: Request) -> operations.Incident:
    """Доступно и без входа: сотрудник сканирует QR-бирку на объекте и пишет, что сломалось."""
    _, plan = _latest(plan_id)
    try:
        return operations.report(store, plan_id, plan, payload, current_user(request))
    except KeyError:
        raise HTTPException(status_code=404, detail="На плане нет такого объекта") from None


@app.patch(
    "/api/plans/{plan_id}/incidents/{incident_id}",
    response_model=operations.Incident,
    summary="Взять обращение в работу или закрыть",
)
def update_incident(
    plan_id: str, incident_id: str, payload: operations.StatusChange, request: Request
) -> operations.Incident:
    user = require(request, "incident.manage")
    try:
        return operations.change_status(store, plan_id, incident_id, payload, user)
    except KeyError:
        raise HTTPException(status_code=404, detail="Обращение не найдено") from None


@app.post("/api/plans/{plan_id}/inspections", response_model=PlanRef, summary="Отметить осмотр выполненным")
def inspection_done(plan_id: str, payload: operations.InspectionDone, request: Request) -> PlanRef:
    """Записывает дату осмотра в паспорт объекта новой версией плана — с именем сотрудника."""
    user = require(request, "inspection.done")
    document, plan = _latest(plan_id)
    try:
        document, following = operations.mark_inspected(document, payload)
    except KeyError:
        raise HTTPException(status_code=404, detail="На плане нет такого объекта") from None
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    label = operations.object_label(plan, payload.object_id)
    note = f"Осмотр «{label}» выполнен, следующий {following}"
    version = store.save_version(plan_id, document, note, author=_author(user))
    return PlanRef(id=plan_id, version=version)


@app.get("/api/plans/{plan_id}/today", response_model=operations.Today, summary="Сводка на сегодня")
def plan_today(plan_id: str, request: Request, day: date | None = None) -> operations.Today:
    """Что сломано, какие осмотры подошли, сколько окон нужно в пик с учётом неисправных.

    day — сегодняшний день по часам сотрудника; без него — в поясе отделения.
    """
    _, plan = _latest(plan_id)
    incidents = operations.list_incidents(store, plan_id)
    return operations.today_summary(plan, incidents, current_user(request), day)


@app.get("/api/network", response_model=network.NetworkReport, summary="Сеть отделений: очередь на ремонт")
def network_priorities() -> network.NetworkReport:
    """Все сохранённые планы — в одну очередь на ремонт с прозрачным баллом."""
    plans = store.list_plans()

    def load(plan_id: str) -> tuple[Plan, ValidationReport]:
        document = store.get(plan_id)
        plan = _parse_plan(document)
        key = fingerprint("validate", document, None)
        return plan, validation_cache.get_or_compute(key, lambda: validate(plan, None))

    def incidents(plan_id: str) -> list[operations.Incident]:
        return operations.list_incidents(store, plan_id)

    return network.build(plans, load, incidents)


_network_demo_lock = threading.Lock()


@app.post("/api/network/demo", summary="Загрузить демонстрационную сеть")
def network_demo() -> dict[str, Any]:
    """Добавляет условные отделения, которых ещё нет среди сохранённых планов (по названию).

    Проверка и вставка — под одной блокировкой: два одновременных нажатия иначе добавляли сеть дважды.
    """
    folder = _network_sample_dir()
    if not folder.exists():
        raise HTTPException(status_code=404, detail="Демонстрационная сеть не входит в эту поставку")
    with _network_demo_lock:
        present = {p["title"] for p in store.list_plans()}
        added = 0
        for path in sorted(folder.glob("*.json")):
            document = json.loads(path.read_text(encoding="utf-8"))
            title = document["ops"]["name"]
            if title in present:
                continue
            _parse_plan(document)
            store.create(title, document, "Демонстрационная сеть")
            present.add(title)
            added += 1
    return {"added": added}


@app.get("/api/plans/{plan_id}/versions", summary="Версии плана")
def plan_versions(plan_id: str) -> list[dict[str, Any]]:
    try:
        return store.list_versions(plan_id)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"План {plan_id} не найден") from None


@app.delete("/api/plans/{plan_id}", status_code=204, summary="Удалить план")
def delete_plan(plan_id: str, request: Request) -> Response:
    """Удаление необратимо, поэтому только после входа: на общем сервере чужой план иначе исчезнет
    от одного случайного нажатия."""
    require(request)
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


class CompareRequest(BaseModel):
    before: dict[str, Any]
    after: dict[str, Any]
    prices: compare_model.Prices = Field(default_factory=compare_model.Prices)


@app.post("/api/compare", response_model=compare_model.Comparison, summary="Сравнить два варианта и посчитать смету")
def compare_variants(payload: CompareRequest) -> compare_model.Comparison:
    before, after = _parse_plan(payload.before), _parse_plan(payload.after)
    key = fingerprint("compare", payload.before, payload.after, payload.prices.model_dump())
    return assistant_cache.get_or_compute(
        key, lambda: _with_slot("сравнение вариантов", lambda: compare_model.compare(before, after, payload.prices))
    )


class PlaceRequest(BaseModel):
    document: dict[str, Any]
    equipment_type: str = Field(min_length=1, description="Код типа оборудования из справочника")
    zone_id: str | None = Field(default=None, description="Ограничить поиск зоной")


@app.post("/api/exchange/1c/export", summary="Опись оборудования для загрузки в 1С")
def exchange_export(payload: SaveVersionRequest) -> Response:
    """Таблица CSV (UTF-8, «;»): инвентарный номер, наименование, серийный номер, местонахождение."""
    plan = _parse_plan(payload.document)
    filename = exchange.export_filename(plan)
    return Response(
        content=exchange.export_csv(plan),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(filename)}"},
    )


@app.post("/api/exchange/1c/reconcile", response_model=exchange.Reconciliation, summary="Сверка плана с учётом 1С")
async def exchange_reconcile(document: str = Form(...), file: UploadFile = File(...)) -> exchange.Reconciliation:
    """Ведомость основных средств из 1С (CSV) против оборудования на плане."""
    try:
        plan = _parse_plan(json.loads(document))
    except json.JSONDecodeError:
        raise HTTPException(status_code=422, detail="План должен быть JSON-документом") from None
    raw = await file.read()
    if len(raw) > 5 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="Файл учёта больше 5 МБ")
    try:
        rows, recognised = exchange.read_accounting(exchange.decode(raw))
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from None
    return exchange.reconcile(plan, rows, recognised)


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


class StorageRequest(BaseModel):
    document: dict[str, Any]
    factor: float = Field(default=1.0, ge=0.3, le=5.0)


@app.post("/api/storage", response_model=parcel_storage.StorageReport, summary="Ёмкость хранения посылок")
def plan_storage(payload: StorageRequest) -> parcel_storage.StorageReport:
    return parcel_storage.analyze(_parse_plan(payload.document), payload.factor)


class PeakRequest(BaseModel):
    document: dict[str, Any]
    scenario: peak.PeakScenario = Field(default_factory=peak.PeakScenario)


@app.post("/api/peak", response_model=peak.PeakReport, summary="Очередь в пик на плане по сценарию")
def plan_peak(payload: PeakRequest) -> peak.PeakReport:
    return peak.analyze(_parse_plan(payload.document), payload.scenario)


MAX_VISION_FRAMES = 180


@app.post("/api/vision/analyze", response_model=vision.VisionReport, summary="Посетители по кадрам камеры")
async def vision_analyze(
    document: str = Form(..., description="План отделения в JSON"),
    calibration: str = Form(
        ...,
        description='Привязка кадра к плану: {"frame": [[x,y]×4], "plan": [[x,y]×4], "frame_size": [ширина, высота]}',
    ),
    seconds: str = Form(..., description="Секунда записи для каждого кадра, JSON-список"),
    person_area_sqm: float = Form(vision.PERSON_AREA_SQM, gt=0.02, le=2.0),
    frames: list[UploadFile] = File(..., description="Кадры записи по порядку"),
) -> vision.VisionReport:
    """Кадры камеры зала: сколько людей, где стоят, хватает ли мест.

    Видео целиком на сервер не загружается: браузер раскладывает запись на кадры и шлёт их
    уменьшенными. Так хватает и бесплатного хостинга, и записи любой длины.
    """
    if not 8 <= len(frames) <= MAX_VISION_FRAMES:
        raise HTTPException(
            status_code=422,
            detail=f"Кадров должно быть от 8 до {MAX_VISION_FRAMES}: пришло {len(frames)}.",
        )
    try:
        plan = _parse_plan(json.loads(document))
        marks = json.loads(calibration)
        moments = [float(value) for value in json.loads(seconds)]
    except (json.JSONDecodeError, TypeError, ValueError):
        raise HTTPException(status_code=422, detail="План, привязка или секунды пришли не в JSON.") from None
    if len(moments) != len(frames):
        raise HTTPException(status_code=422, detail="На каждый кадр нужна его секунда записи.")

    payload = [await frame.read() for frame in frames]
    try:
        images, source = vision.decode_frames(payload)
        size = tuple(marks.get("frame_size") or source)
        matrix = vision.homography(
            [tuple(point) for point in marks["frame"]], [tuple(point) for point in marks["plan"]]
        )
        return vision.analyze(plan, images, moments, matrix, size, person_area_sqm)
    except vision.VisionError as error:
        # Разбор сам объясняет, что не так с записью или привязкой: его текст и показывается.
        # Эта ветка обязана стоять раньше общей: VisionError — это ValueError.
        raise HTTPException(status_code=422, detail=str(error)) from None
    except (KeyError, TypeError, ValueError):
        # Привязка приходит из браузера, но руками её присылают и кривой: четыре точки по два числа.
        raise HTTPException(
            status_code=422,
            detail="В привязке нужны поля frame и plan: по четыре точки, в каждой два числа.",
        ) from None


@app.post("/api/capacity/log", response_model=queue_log.QueueLogReport, summary="Очередь по журналу или счётчику")
async def plan_queue_log(document: str = Form(...), file: UploadFile = File(...)) -> queue_log.QueueLogReport:
    """Журнал электронной очереди или почасовой счётчик посетителей (CSV) против окон на плане."""
    try:
        plan = _parse_plan(json.loads(document))
    except json.JSONDecodeError:
        raise HTTPException(status_code=422, detail="План должен быть JSON-документом") from None
    raw = await file.read()
    try:
        source, records, skipped = queue_log.read(exchange.decode(raw))
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from None
    return queue_log.analyze(plan, source, records, skipped)


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
    plan_id: str | None = Field(default=None, description="Сохранённый план — чтобы учесть обращения о неисправностях")
    text: str = Field(min_length=1, max_length=500, description="Команда словами, например «поставь две кассы у перегородки»")


@app.post("/api/assistant/command", response_model=recommend.CommandResult, summary="Команда словами")
def assistant_command(payload: CommandRequest) -> recommend.CommandResult:
    """Разбирает фразу и готовит рекомендации: план меняется только после согласия человека."""
    plan = _parse_plan(payload.document)
    incidents = None
    if payload.plan_id:
        incidents = [i.model_dump() for i in operations.list_incidents(store, payload.plan_id)]
    key = fingerprint("command", payload.document, payload.text, incidents)
    return assistant_cache.get_or_compute(
        key, lambda: _with_slot("разбор команды", lambda: recommend.run_command(plan, payload.text, incidents))
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
