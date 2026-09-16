"""Хранилище планов: версии, чтение, запись.

Каждое сохранение создаёт новую версию, старые остаются доступными. Это нужно, чтобы
сравнивать планировку до и после изменения и показывать эффект рекомендации.
"""

from __future__ import annotations

import json
import sqlite3
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
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class PlanStore:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.executescript(SCHEMA)

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    def create(self, title: str, document: dict[str, Any], note: str = "") -> tuple[str, int]:
        plan_id = uuid.uuid4().hex
        with self._connect() as conn:
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

    def save_version(self, plan_id: str, document: dict[str, Any], note: str = "") -> int:
        with self._connect() as conn:
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
        with self._connect() as conn:
            cur = conn.execute("DELETE FROM plans WHERE id = ?", (plan_id,))
            if cur.rowcount == 0:
                raise KeyError(plan_id)
