"""Хранилище планов: версии, чтение, запись.

Каждое сохранение создаёт новую версию, старые остаются доступными. Это нужно, чтобы
сравнивать планировку до и после изменения и показывать эффект рекомендации.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SCHEMA = """
CREATE TABLE IF NOT EXISTS plans (
    id          TEXT PRIMARY KEY,
    title       TEXT NOT NULL,
    created_at  TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS versions (
    plan_id     TEXT NOT NULL REFERENCES plans(id) ON DELETE CASCADE,
    version     INTEGER NOT NULL,
    document    TEXT NOT NULL,
    note        TEXT NOT NULL DEFAULT '',
    created_at  TEXT NOT NULL,
    PRIMARY KEY (plan_id, version)
);
CREATE TABLE IF NOT EXISTS records (
    kind        TEXT NOT NULL,
    id          TEXT NOT NULL,
    scope       TEXT NOT NULL DEFAULT '',
    data        TEXT NOT NULL,
    updated_at  TEXT NOT NULL,
    PRIMARY KEY (kind, id)
);
CREATE INDEX IF NOT EXISTS records_scope ON records (kind, scope);
"""


BUSY_TIMEOUT_S = 10.0


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class _SqliteRecords:
    """Записи сервиса рядом с планами: пользователи, сеансы, обращения сотрудников.

    Каждая запись — документ JSON с видом (kind) и областью (scope, например план отделения).
    Планы и их версии здесь не хранятся: у них своя схема.
    """

    def put_record(self, kind: str, record_id: str, data: dict[str, Any], scope: str = "") -> None:
        with self._write_lock, self._connect() as conn:  # type: ignore[attr-defined]
            conn.execute(
                "INSERT INTO records (kind, id, scope, data, updated_at) VALUES (?, ?, ?, ?, ?) "
                "ON CONFLICT(kind, id) DO UPDATE SET scope = excluded.scope, data = excluded.data, "
                "updated_at = excluded.updated_at",
                (kind, record_id, scope, json.dumps(data, ensure_ascii=False), _now()),
            )

    def get_record(self, kind: str, record_id: str) -> dict[str, Any] | None:
        with self._connect() as conn:  # type: ignore[attr-defined]
            row = conn.execute("SELECT data FROM records WHERE kind = ? AND id = ?", (kind, record_id)).fetchone()
        return json.loads(row["data"]) if row else None

    def list_records(self, kind: str, scope: str | None = None) -> list[dict[str, Any]]:
        with self._connect() as conn:  # type: ignore[attr-defined]
            if scope is None:
                rows = conn.execute("SELECT data FROM records WHERE kind = ? ORDER BY updated_at DESC", (kind,)).fetchall()
            else:
                rows = conn.execute(
                    "SELECT data FROM records WHERE kind = ? AND scope = ? ORDER BY updated_at DESC", (kind, scope)
                ).fetchall()
        return [json.loads(row["data"]) for row in rows]

    def delete_record(self, kind: str, record_id: str) -> None:
        with self._write_lock, self._connect() as conn:  # type: ignore[attr-defined]
            conn.execute("DELETE FROM records WHERE kind = ? AND id = ?", (kind, record_id))


class PlanStore(_SqliteRecords):
    """Хранилище планов в SQLite.

    Обработчики FastAPI выполняются в пуле потоков, поэтому две записи могут прийти
    одновременно. Журнал WAL разводит читателей и писателей, а взаимное исключение на запись
    выстраивает сами записи в очередь: иначе вторая получает «database is locked».
    """

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._write_lock = threading.Lock()
        with self._connect() as conn:
            conn.executescript(SCHEMA)

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=BUSY_TIMEOUT_S)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        # Журнал упреждающей записи: чтение не ждёт запись, запись не ждёт чтение.
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA synchronous = NORMAL")
        return conn

    def create(self, title: str, document: dict[str, Any], note: str = "") -> tuple[str, int]:
        plan_id = uuid.uuid4().hex
        with self._write_lock, self._connect() as conn:
            conn.execute(
                "INSERT INTO plans (id, title, created_at) VALUES (?, ?, ?)",
                (plan_id, title, _now()),
            )
            conn.execute(
                "INSERT INTO versions (plan_id, version, document, note, created_at) "
                "VALUES (?, 1, ?, ?, ?)",
                (plan_id, json.dumps(document, ensure_ascii=False), note, _now()),
            )
        return plan_id, 1

    def save_version(
        self, plan_id: str, document: dict[str, Any], note: str = "", title: str | None = None
    ) -> int:
        """Новая версия плана. title обновляет название плана в списке, если оно изменилось."""
        with self._write_lock, self._connect() as conn:
            row = conn.execute(
                "SELECT COALESCE(MAX(version), 0) AS v FROM versions WHERE plan_id = ?",
                (plan_id,),
            ).fetchone()
            if conn.execute("SELECT 1 FROM plans WHERE id = ?", (plan_id,)).fetchone() is None:
                raise KeyError(plan_id)
            version = int(row["v"]) + 1
            conn.execute(
                "INSERT INTO versions (plan_id, version, document, note, created_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (plan_id, version, json.dumps(document, ensure_ascii=False), note, _now()),
            )
            if title:
                conn.execute("UPDATE plans SET title = ? WHERE id = ?", (title, plan_id))
        return version

    def get(self, plan_id: str, version: int | None = None) -> dict[str, Any]:
        with self._connect() as conn:
            if version is None:
                row = conn.execute(
                    "SELECT document FROM versions WHERE plan_id = ? "
                    "ORDER BY version DESC LIMIT 1",
                    (plan_id,),
                ).fetchone()
            else:
                row = conn.execute(
                    "SELECT document FROM versions WHERE plan_id = ? AND version = ?",
                    (plan_id, version),
                ).fetchone()
        if row is None:
            raise KeyError(plan_id)
        return json.loads(row["document"])

    def list_plans(self) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT p.id, p.title, p.created_at, MAX(v.version) AS versions "
                "FROM plans p JOIN versions v ON v.plan_id = p.id "
                "GROUP BY p.id ORDER BY p.created_at DESC"
            ).fetchall()
        return [dict(row) for row in rows]

    def list_versions(self, plan_id: str) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT version, note, created_at FROM versions WHERE plan_id = ? "
                "ORDER BY version",
                (plan_id,),
            ).fetchall()
        if not rows:
            raise KeyError(plan_id)
        return [dict(row) for row in rows]

    def delete(self, plan_id: str) -> None:
        with self._write_lock, self._connect() as conn:
            cur = conn.execute("DELETE FROM plans WHERE id = ?", (plan_id,))
            if cur.rowcount == 0:
                raise KeyError(plan_id)


PG_SCHEMA = (
    """
    CREATE TABLE IF NOT EXISTS plans (
        id          TEXT PRIMARY KEY,
        title       TEXT NOT NULL,
        created_at  TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS versions (
        plan_id     TEXT NOT NULL REFERENCES plans(id) ON DELETE CASCADE,
        version     INTEGER NOT NULL,
        document    TEXT NOT NULL,
        note        TEXT NOT NULL DEFAULT '',
        created_at  TEXT NOT NULL,
        PRIMARY KEY (plan_id, version)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS records (
        kind        TEXT NOT NULL,
        id          TEXT NOT NULL,
        scope       TEXT NOT NULL DEFAULT '',
        data        TEXT NOT NULL,
        updated_at  TEXT NOT NULL,
        PRIMARY KEY (kind, id)
    )
    """,
    "CREATE INDEX IF NOT EXISTS records_scope ON records (kind, scope)",
)


class PostgresPlanStore:
    """Хранилище планов в PostgreSQL — для развёртывания, где диск не переживает перезапуск.

    Интерфейс тот же, что у PlanStore. Документ хранится текстом, как пришёл, а не в JSONB:
    JSONB переставляет ключи, и сохранённый план открывался бы не в том виде, в каком его
    сохранили. Номер новой версии выдаётся под блокировкой строки плана, поэтому два
    одновременных сохранения из разных процессов не получат один и тот же номер.
    """

    def __init__(self, url: str) -> None:
        from urllib.parse import parse_qs, unquote, urlsplit

        parts = urlsplit(url)
        if parts.scheme not in {"postgres", "postgresql"}:
            raise ValueError(f"Ожидался адрес postgres://, получен {parts.scheme}://")
        query = parse_qs(parts.query)
        self._params: dict[str, Any] = {
            "user": unquote(parts.username or ""),
            "password": unquote(parts.password or "") or None,
            "host": parts.hostname or "localhost",
            "port": parts.port or 5432,
            "database": (parts.path or "/").lstrip("/") or "postgres",
            "timeout": BUSY_TIMEOUT_S,
        }
        socket_dir = query.get("host", [None])[0]
        if socket_dir and socket_dir.startswith("/"):
            self._params["unix_sock"] = f"{socket_dir}/.s.PGSQL.{self._params['port']}"
            self._params.pop("host")
        elif query.get("sslmode", [""])[0] in {"require", "verify-ca", "verify-full"}:
            import ssl

            self._params["ssl_context"] = ssl.create_default_context()
        self._write_lock = threading.Lock()
        conn = self._connect()
        try:
            for statement in PG_SCHEMA:
                conn.run(statement)
        finally:
            conn.close()

    def _connect(self):
        import pg8000.native

        return pg8000.native.Connection(**self._params)

    @staticmethod
    def _rows(conn, sql: str, **params: Any) -> list[dict[str, Any]]:
        rows = conn.run(sql, **params) or []
        names = [column["name"] for column in (conn.columns or [])]
        return [dict(zip(names, row)) for row in rows]

    def _transaction(self, work):
        """Выполняет работу в одной транзакции: либо всё, либо ничего."""
        conn = self._connect()
        try:
            conn.run("START TRANSACTION")
            try:
                result = work(conn)
            except BaseException:
                conn.run("ROLLBACK")
                raise
            conn.run("COMMIT")
            return result
        finally:
            conn.close()

    def create(self, title: str, document: dict[str, Any], note: str = "") -> tuple[str, int]:
        plan_id = uuid.uuid4().hex

        def work(conn) -> None:
            conn.run("INSERT INTO plans (id, title, created_at) VALUES (:id, :title, :at)", id=plan_id, title=title, at=_now())
            conn.run(
                "INSERT INTO versions (plan_id, version, document, note, created_at) "
                "VALUES (:id, 1, :doc, :note, :at)",
                id=plan_id,
                doc=json.dumps(document, ensure_ascii=False),
                note=note,
                at=_now(),
            )

        with self._write_lock:
            self._transaction(work)
        return plan_id, 1

    def save_version(
        self, plan_id: str, document: dict[str, Any], note: str = "", title: str | None = None
    ) -> int:
        def work(conn) -> int:
            if not conn.run("SELECT 1 FROM plans WHERE id = :id FOR UPDATE", id=plan_id):
                raise KeyError(plan_id)
            current = conn.run("SELECT COALESCE(MAX(version), 0) FROM versions WHERE plan_id = :id", id=plan_id)
            version = int(current[0][0]) + 1
            conn.run(
                "INSERT INTO versions (plan_id, version, document, note, created_at) "
                "VALUES (:id, :v, :doc, :note, :at)",
                id=plan_id,
                v=version,
                doc=json.dumps(document, ensure_ascii=False),
                note=note,
                at=_now(),
            )
            if title:
                conn.run("UPDATE plans SET title = :title WHERE id = :id", title=title, id=plan_id)
            return version

        with self._write_lock:
            return self._transaction(work)

    def get(self, plan_id: str, version: int | None = None) -> dict[str, Any]:
        conn = self._connect()
        try:
            if version is None:
                rows = conn.run(
                    "SELECT document FROM versions WHERE plan_id = :id ORDER BY version DESC LIMIT 1", id=plan_id
                )
            else:
                rows = conn.run(
                    "SELECT document FROM versions WHERE plan_id = :id AND version = :v", id=plan_id, v=version
                )
        finally:
            conn.close()
        if not rows:
            raise KeyError(plan_id)
        return json.loads(rows[0][0])

    def list_plans(self) -> list[dict[str, Any]]:
        conn = self._connect()
        try:
            return self._rows(
                conn,
                "SELECT p.id, p.title, p.created_at, MAX(v.version) AS versions "
                "FROM plans p JOIN versions v ON v.plan_id = p.id "
                "GROUP BY p.id ORDER BY p.created_at DESC",
            )
        finally:
            conn.close()

    def list_versions(self, plan_id: str) -> list[dict[str, Any]]:
        conn = self._connect()
        try:
            rows = self._rows(
                conn,
                "SELECT version, note, created_at FROM versions WHERE plan_id = :id ORDER BY version",
                id=plan_id,
            )
        finally:
            conn.close()
        if not rows:
            raise KeyError(plan_id)
        return rows

    def delete(self, plan_id: str) -> None:
        def work(conn) -> None:
            conn.run("DELETE FROM plans WHERE id = :id", id=plan_id)
            if conn.row_count == 0:
                raise KeyError(plan_id)

        with self._write_lock:
            self._transaction(work)


def _pg_records(cls):
    """Те же методы записей для PostgreSQL."""

    def put_record(self, kind: str, record_id: str, data: dict[str, Any], scope: str = "") -> None:
        def work(conn) -> None:
            conn.run(
                "INSERT INTO records (kind, id, scope, data, updated_at) VALUES (:kind, :id, :scope, :data, :at) "
                "ON CONFLICT (kind, id) DO UPDATE SET scope = EXCLUDED.scope, data = EXCLUDED.data, "
                "updated_at = EXCLUDED.updated_at",
                kind=kind, id=record_id, scope=scope, data=json.dumps(data, ensure_ascii=False), at=_now(),
            )

        with self._write_lock:
            self._transaction(work)

    def get_record(self, kind: str, record_id: str) -> dict[str, Any] | None:
        conn = self._connect()
        try:
            rows = self._rows(conn, "SELECT data FROM records WHERE kind = :kind AND id = :id", kind=kind, id=record_id)
        finally:
            conn.close()
        return json.loads(rows[0]["data"]) if rows else None

    def list_records(self, kind: str, scope: str | None = None) -> list[dict[str, Any]]:
        conn = self._connect()
        try:
            if scope is None:
                rows = self._rows(conn, "SELECT data FROM records WHERE kind = :kind ORDER BY updated_at DESC", kind=kind)
            else:
                rows = self._rows(
                    conn,
                    "SELECT data FROM records WHERE kind = :kind AND scope = :scope ORDER BY updated_at DESC",
                    kind=kind,
                    scope=scope,
                )
        finally:
            conn.close()
        return [json.loads(row["data"]) for row in rows]

    def delete_record(self, kind: str, record_id: str) -> None:
        def work(conn) -> None:
            conn.run("DELETE FROM records WHERE kind = :kind AND id = :id", kind=kind, id=record_id)

        with self._write_lock:
            self._transaction(work)

    cls.put_record = put_record
    cls.get_record = get_record
    cls.list_records = list_records
    cls.delete_record = delete_record
    return cls


_pg_records(PostgresPlanStore)


def open_store(database_url: str | None, sqlite_path: str | Path) -> PlanStore | PostgresPlanStore:
    """Хранилище по настройке: адрес PostgreSQL, если задан, иначе файл SQLite."""
    if database_url:
        return PostgresPlanStore(database_url)
    return PlanStore(sqlite_path)
