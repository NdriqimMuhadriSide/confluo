from alembic import context
from sqlalchemy import create_engine, pool

from confluo_core.settings import get_settings


def _url() -> str:
    # `-x url=...` (used by tests) overrides the configured owner URL.
    url = context.get_x_argument(as_dictionary=True).get("url")
    url = url or str(get_settings().migrations_database_url)
    return url.replace("postgresql://", "postgresql+psycopg://", 1)


def run_migrations() -> None:
    engine = create_engine(_url(), poolclass=pool.NullPool)
    with engine.connect() as connection:
        context.configure(connection=connection, transaction_per_migration=True)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    raise SystemExit("offline migrations are not supported; run against a database")
run_migrations()
