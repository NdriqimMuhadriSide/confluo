from psycopg_pool import AsyncConnectionPool

from confluo_core.settings import Settings


def create_pool(settings: Settings) -> AsyncConnectionPool:
    """Connection pool for the app database. Open it with `await pool.open()`."""
    return AsyncConnectionPool(
        conninfo=str(settings.database_url),
        min_size=1,
        max_size=10,
        open=False,
        kwargs={"autocommit": True},
    )


async def ping(pool: AsyncConnectionPool) -> bool:
    try:
        async with pool.connection(timeout=2) as conn:
            await conn.execute("select 1")
    except Exception:
        return False
    return True
