"""Сотрудники и вход в сервис.

Без входа сервис работает как раньше: план открывается, правится и проверяется. Вход нужен для
того, что делает конкретный человек: начальник отделения видит сводку «Сегодня», инженер берёт
обращение в работу и отмечает осмотр, а в журнале остаётся, кто это сделал.

Пароль хранится только как хеш PBKDF2-SHA256 с солью. Сеанс — случайный токен; в базе лежит его
отпечаток, а не сам токен, поэтому утечка базы не даёт войти. Неудачные попытки входа
ограничены, чтобы пароль нельзя было подобрать перебором.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import secrets
import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Protocol

from pydantic import BaseModel

HASH_ITERATIONS = 200_000
SESSION_HOURS = 12
MAX_FAILURES = 5
FAILURE_WINDOW_S = 300

ROLES = {
    "head": "Начальник отделения",
    "operator": "Оператор связи",
    "engineer": "Инженер эксплуатации",
    "designer": "Проектировщик УФПС",
}

# Что может каждая роль сверх того, что доступно всем.
PERMISSIONS = {
    "incident.manage": {"head", "engineer"},
    "inspection.done": {"head", "engineer"},
}

# Демо-сотрудники для показа. Пароль задаётся переменной OPS_TWIN_DEMO_PASSWORD; значение по
# умолчанию указано в README в разделе «Вход». OPS_TWIN_DEMO_USERS=0 выключает демо-входы.
DEMO_USERS = (
    {"login": "nachalnik", "name": "Морозова Ирина Сергеевна", "role": "head"},
    {"login": "operator", "name": "Смирнова Анна Викторовна", "role": "operator"},
    {"login": "inzhener", "name": "Ковалёв Дмитрий Андреевич", "role": "engineer"},
    {"login": "proektirovshik", "name": "Белова Мария Олеговна", "role": "designer"},
)


class Records(Protocol):
    def put_record(self, kind: str, record_id: str, data: dict[str, Any], scope: str = "") -> None: ...
    def get_record(self, kind: str, record_id: str) -> dict[str, Any] | None: ...
    def delete_record(self, kind: str, record_id: str) -> None: ...


class User(BaseModel):
    login: str
    name: str
    role: str
    role_label: str


class Session(BaseModel):
    token: str
    user: User


def _hash(password: str, salt: bytes) -> str:
    return hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, HASH_ITERATIONS).hex()


def _fingerprint(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def demo_enabled() -> bool:
    return os.environ.get("OPS_TWIN_DEMO_USERS", "1") != "0"


def demo_password() -> str:
    return os.environ.get("OPS_TWIN_DEMO_PASSWORD", "pochta-demo")


def _public(record: dict[str, Any]) -> User:
    return User(
        login=record["login"],
        name=record["name"],
        role=record["role"],
        role_label=ROLES.get(record["role"], record["role"]),
    )


class Accounts:
    def __init__(self, store: Records) -> None:
        self.store = store
        self._failures: dict[str, list[float]] = {}
        self._lock = threading.Lock()

    def ensure_demo_users(self) -> None:
        if not demo_enabled():
            return
        for person in DEMO_USERS:
            if self.store.get_record("user", person["login"]) is None:
                self.create_user(person["login"], person["name"], person["role"], demo_password())

    def create_user(self, login: str, name: str, role: str, password: str) -> User:
        if role not in ROLES:
            raise ValueError(f"Неизвестная роль: {role}")
        salt = secrets.token_bytes(16)
        record = {"login": login, "name": name, "role": role, "salt": salt.hex(), "hash": _hash(password, salt)}
        self.store.put_record("user", login, record)
        return _public(record)

    def _blocked(self, login: str) -> bool:
        now = time.monotonic()
        with self._lock:
            recent = [t for t in self._failures.get(login, []) if now - t < FAILURE_WINDOW_S]
            self._failures[login] = recent
            return len(recent) >= MAX_FAILURES

    def _fail(self, login: str) -> None:
        with self._lock:
            self._failures.setdefault(login, []).append(time.monotonic())

    def login(self, login: str, password: str) -> Session:
        """Вход по паролю. PermissionError — неверная пара, RuntimeError — слишком много попыток."""
        login = login.strip().lower()
        if self._blocked(login):
            raise RuntimeError("Слишком много неудачных попыток. Подождите 5 минут.")
        record = self.store.get_record("user", login)
        expected = record["hash"] if record else _hash(password, b"0" * 16)
        actual = _hash(password, bytes.fromhex(record["salt"])) if record else ""
        # Сравнение за постоянное время: по задержке нельзя понять, совпало ли начало.
        if record is None or not hmac.compare_digest(expected, actual):
            self._fail(login)
            raise PermissionError("Неверный логин или пароль.")
        return self._open(record)

    def demo_login(self, role: str) -> Session:
        """Вход демо-сотрудником одной кнопкой — только пока демо-входы включены."""
        if not demo_enabled():
            raise PermissionError("Демо-входы выключены.")
        person = next((p for p in DEMO_USERS if p["role"] == role), None)
        if person is None:
            raise PermissionError("Нет демо-сотрудника с такой ролью.")
        self.ensure_demo_users()
        record = self.store.get_record("user", person["login"])
        assert record is not None
        return self._open(record)

    def _open(self, record: dict[str, Any]) -> Session:
        token = secrets.token_urlsafe(32)
        expires = datetime.now(timezone.utc) + timedelta(hours=SESSION_HOURS)
        self.store.put_record("session", _fingerprint(token), {"login": record["login"], "expires": expires.isoformat()})
        return Session(token=token, user=_public(record))

    def user_by_token(self, token: str | None) -> User | None:
        if not token:
            return None
        session = self.store.get_record("session", _fingerprint(token))
        if session is None:
            return None
        if datetime.fromisoformat(session["expires"]) < datetime.now(timezone.utc):
            self.store.delete_record("session", _fingerprint(token))
            return None
        record = self.store.get_record("user", session["login"])
        return _public(record) if record else None

    def logout(self, token: str | None) -> None:
        if token:
            self.store.delete_record("session", _fingerprint(token))


def allowed(user: User | None, permission: str) -> bool:
    return user is not None and user.role in PERMISSIONS.get(permission, set())
