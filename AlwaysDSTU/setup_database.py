"""Создаёт базу Always DSTU при её отсутствии и применяет схему PostgreSQL."""

from __future__ import annotations

import os

from database import PostgresRepository, _prepare_libpq_dll_search, connection_params


def main() -> int:
    target_name = os.environ.get("PGDATABASE", "always_dstu")
    admin_name = os.environ.get("PGADMIN_DATABASE", "postgres")
    if target_name == admin_name:
        print("PGDATABASE и PGADMIN_DATABASE должны быть разными базами.")
        return 1

    _prepare_libpq_dll_search()
    try:
        import psycopg
        from psycopg import sql
    except (ImportError, OSError):
        print(
            "Не удалось загрузить psycopg или libpq. Установите зависимости и "
            "проверьте путь PostgreSQL (при необходимости задайте PG_LIBDIR)."
        )
        return 1

    try:
        admin_params = connection_params(admin_name)
        with psycopg.connect(**admin_params, autocommit=True) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT 1 FROM pg_database WHERE datname = %s", (target_name,)
                )
                if cursor.fetchone() is None:
                    cursor.execute(
                        sql.SQL("CREATE DATABASE {} ").format(sql.Identifier(target_name))
                    )
                    print(f"Создана база данных {target_name}.")
                else:
                    print(f"База данных {target_name} уже существует.")

        PostgresRepository().initialize_schema()
        print("Схема Always DSTU создана или уже актуальна.")
        return 0
    except (OSError, RuntimeError, psycopg.Error) as exc:
        print(f"Не удалось настроить PostgreSQL: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
