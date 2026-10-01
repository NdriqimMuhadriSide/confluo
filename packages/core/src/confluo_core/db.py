from psycopg import AsyncConnection
from psycopg_pool import AsyncConnectionPool

from confluo_core.settings import Settings


async def _autocommit(conn: AsyncConnection) -> None:
    # Set here rather than in `kwargs`: Procrastinate opens its LISTEN connection with
    # the pool's kwargs plus its own autocommit=True, and a duplicate crashes it.
    await conn.set_autocommit(True)


def make_pool(conninfo: str, *, min_size: int = 1, max_size: int = 10) -> AsyncConnectionPool:
    """An autocommit pool (transactions are always explicit). Open with `await pool.open()`."""
    return AsyncConnectionPool(
        conninfo=conninfo, min_size=min_size, max_size=max_size, open=False, configure=_autocommit
    )


def create_pool(settings: Settings) -> AsyncConnectionPool:
    """Connection pool for the app database."""
    return make_pool(str(settings.database_url))


async def ping(pool: AsyncConnectionPool) -> bool:
    try:
        async with pool.connection(timeout=2) as conn:
            await conn.execute("select 1")
    except Exception:
        return False
    return True
