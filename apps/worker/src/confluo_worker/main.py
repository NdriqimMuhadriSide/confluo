"""Background worker: runs Procrastinate jobs from core and the installed modules.

Jobs get the process pool (tenant transactions) and the webhook providers through
the job context.
"""

import asyncio
import logging

from confluo_core.db import create_pool
from confluo_core.job_app import build_job_app
from confluo_core.jobs import LLM_KEY, job_settings_summary, worker_context
from confluo_core.llm import LLMGateway
from confluo_core.logging import configure_logging
from confluo_core.modules import discover_modules
from confluo_core.settings import get_settings
from confluo_core.webhooks import PROVIDERS_KEY, default_providers

log = logging.getLogger("confluo.worker")


async def main() -> None:
    settings = get_settings()
    configure_logging(settings.log_level)
    modules = discover_modules()
    app = build_job_app(modules)
    pool = create_pool(settings)
    await pool.open()
    try:
        async with app.open_async(pool):
            log.info(
                "worker started: modules %s, tasks %s, %s",
                ", ".join(modules) or "none",
                ", ".join(sorted(app.tasks)),
                job_settings_summary(settings),
            )
            context = worker_context(
                pool,
                **{
                    PROVIDERS_KEY: default_providers(settings, modules),
                    LLM_KEY: LLMGateway(settings, pool),
                },
            )
            await app.run_worker_async(additional_context=context)
    finally:
        await pool.close()
        log.info("worker stopped")


def run() -> None:
    asyncio.run(main())
