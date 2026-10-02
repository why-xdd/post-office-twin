"""Завести сотрудника: python scripts/add_user.py <логин> "<ФИО>" <роль>

Пароль спрашивается без отображения на экране. Роли: head, operator, engineer, designer.
База — та же, что у сервиса: DATABASE_URL или OPS_TWIN_DB.
"""

from __future__ import annotations

import getpass
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.accounts import ROLES, Accounts  # noqa: E402
from app.storage import open_store  # noqa: E402


def main() -> None:
    if len(sys.argv) != 4 or sys.argv[3] not in ROLES:
        print(__doc__)
        sys.exit(2)
    login, name, role = sys.argv[1].strip().lower(), sys.argv[2].strip(), sys.argv[3]
    password = getpass.getpass("Пароль: ")
    if len(password) < 8 or password != getpass.getpass("Ещё раз: "):
        print("Пароли не совпадают или короче 8 знаков.")
        sys.exit(1)
    # Путь к базе тот же, что у сервиса: относительно папки services/api.
    os.chdir(Path(__file__).resolve().parents[1])
    store = open_store(os.environ.get("DATABASE_URL"), os.environ.get("OPS_TWIN_DB", "storage/plans.sqlite"))
    user = Accounts(store).create_user(login, name, role, password)
    print(f"Готово: {user.name}, {user.role_label}, логин {user.login}")


if __name__ == "__main__":
    main()
