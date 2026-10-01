"""Background worker process.

For now it only proves the process runs and can reach the database. The
Postgres-backed job queue (Procrastinate) replaces the heartbeat loop in the
"Webhook ingress ledger + job queue + retries" card.
"""

import asyncio
import contextlib
import logging
import signal

from confluo_core.db import create_pool, ping
from confluo_core.logging import configure_logging
from confluo_core.modules import discover_modules
from confluo_core.settings import Settings, get_settings

log = logging.getLogger("confluo.worker")


async def serve(settings: Settings, stop: asyncio.Event) -> None:
    modules = discover_modules()
    log.info("worker started with modules: %s", ", ".join(modules) or "none")
    pool = create_pool(settings)
    await pool.open(wait=False)
    try:
        while not stop.is_set():
            log.info("heartbeat (database %s)", "ok" if await ping(pool) else "unreachable")
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(stop.wait(), timeout=settings.worker_heartbeat_seconds)
    finally:
        await pool.close()
        log.info("worker stopped")


async def main() -> None:
    settings = get_settings()
    configure_logging(settings.log_level)
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop.set)
    await serve(settings, stop)


def run() -> None:
    asyncio.run(main())
