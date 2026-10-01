"""Let `confluo_app` log in, with the password from CONFLUO_APP_DB_PASSWORD.

The migration creates the role without LOGIN so no password lives in migrations.
Run after `alembic upgrade head` (`make migrate` does both).
"""

import psycopg
from psycopg import sql

from confluo_core.settings import get_settings


def main() -> None:
    settings = get_settings()
    password = settings.app_db_password.get_secret_value()
    if settings.env in ("staging", "production") and password == "confluo_app":
        raise SystemExit("refusing to use the local default password outside local/test")
    with psycopg.connect(str(settings.migrations_database_url), autocommit=True) as conn:
        conn.execute(
            sql.SQL("alter role confluo_app login password {}").format(sql.Literal(password))
        )
    print("confluo_app can log in")


if __name__ == "__main__":
    main()
