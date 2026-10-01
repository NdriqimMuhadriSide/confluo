"""Background jobs on Procrastinate (ARCHITECTURE.md §1, "Job queue").

Tasks are declared on a `TaskSet` at import time (`core_tasks` here, one per module)
and registered as "<namespace>:<name>" on a fresh `procrastinate.App` by
`create_job_app`, which the API (to defer and retry) and the worker (to run) both
use, on the same pool as the rest of the process. (Procrastinate Blueprints rename
their tasks in place when added to an app, so they can't serve several apps in one
process, which tests need.)

- `tenant_task`: the job's `tenant_id` argument opens a tenant transaction (actor
  "system"), so a job only ever sees its own tenant's rows.
- Deferring inside an open transaction (`defer_in(conn, task, ...)`) commits the job
  together with the data that caused it, or neither.
- Retries: exponential backoff, `CONFLUO_JOB_MAX_ATTEMPTS` attempts in total; a job
  that fails its last attempt stays `failed` (dead letter) until retried by hand
  from System health.
"""

import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

import procrastinate
from procrastinate.jobs import Job
from procrastinate.retry import BaseRetryStrategy, RetryDecision
from psycopg import AsyncConnection
from psycopg_pool import AsyncConnectionPool

from confluo_core.settings import Settings, get_settings
from confluo_core.tenancy import tenant_transaction

log = logging.getLogger("confluo.jobs")

POOL_KEY = "pool"


class SettingsRetry(BaseRetryStrategy):
    """Exponential backoff read from settings at retry time (tests use 0 seconds)."""

    def get_retry_decision(self, *, exception: BaseException, job: Job) -> RetryDecision | None:
        settings = get_settings()
        if job.attempts + 1 >= settings.job_max_attempts:
            return None
        wait = settings.job_retry_base_seconds * (2**job.attempts)
        return RetryDecision(retry_in={"seconds": int(wait)})


RETRY = SettingsRetry()


@dataclass(frozen=True)
class TaskSpec:
    name: str
    fn: Callable[..., Awaitable[Any]]
    queue: str
    pass_context: bool


@dataclass
class TaskSet:
    """Task declarations, registered on each App by create_job_app."""

    specs: list[TaskSpec] = field(default_factory=list)

    def task(
        self, *, name: str, queue: str = "default", pass_context: bool = False
    ) -> Callable[[Callable[..., Awaitable[Any]]], Callable[..., Awaitable[Any]]]:
        def decorate(fn: Callable[..., Awaitable[Any]]) -> Callable[..., Awaitable[Any]]:
            self.specs.append(TaskSpec(name, fn, queue, pass_context))
            return fn

        return decorate


core_tasks = TaskSet()


async def record_failure(
    pool: AsyncConnectionPool,
    context: procrastinate.JobContext,
    tenant_id: UUID,
    exc: BaseException,
) -> None:
    """Keep the error for System health, in its own transaction (the job's rolled back)."""
    job = context.job
    async with tenant_transaction(pool, tenant_id) as conn:
        await conn.execute(
            "insert into job_error (job_id, task_name, attempts, error) values (%s, %s, %s, %s)"
            " on conflict (job_id) do update set attempts = excluded.attempts,"
            " error = excluded.error, failed_at = now()",
            (job.id, job.task_name, job.attempts + 1, f"{type(exc).__name__}: {exc}"[:2000]),
        )


def tenant_task(
    tasks: TaskSet, *, name: str, queue: str = "default"
) -> Callable[[Callable[..., Awaitable[Any]]], Any]:
    """Declare a job that runs inside its tenant: `fn(conn, **kwargs)`, deferred with
    `tenant_id=...` plus the kwargs."""

    def decorate(fn: Callable[..., Awaitable[Any]]) -> Any:
        @tasks.task(name=name, queue=queue, pass_context=True)
        async def run(context: procrastinate.JobContext, /, tenant_id: str, **kwargs: Any) -> Any:
            pool: AsyncConnectionPool = context.additional_context[POOL_KEY]
            tenant = UUID(tenant_id)
            try:
                async with tenant_transaction(pool, tenant) as conn:
                    return await fn(conn, **kwargs)
            except Exception as exc:
                log.warning("job %s (%s) failed: %s", context.job.id, name, exc)
                await record_failure(pool, context, tenant, exc)
                raise

        return run

    return decorate


async def defer_in(conn: AsyncConnection, task: Any, **kwargs: Any) -> int:
    """Enqueue `task` on `conn`'s open transaction: the job exists only if it commits."""
    job_id: int = await task.configure(connection=conn).defer_async(**kwargs)
    return job_id


def create_job_app(task_sets: dict[str, TaskSet]) -> procrastinate.App:
    """A Procrastinate app with the given task sets, opened later on the process pool
    with `await app.open_async(pool)`."""
    app = procrastinate.App(connector=procrastinate.PsycopgConnector())
    for namespace, task_set in task_sets.items():
        for spec in task_set.specs:
            register: Any = app.task  # overloads depend on a literal pass_context
            register(
                name=f"{namespace}:{spec.name}",
                queue=spec.queue,
                pass_context=spec.pass_context,
                retry=RETRY,
            )(spec.fn)
    return app


def worker_context(pool: AsyncConnectionPool, **extra: Any) -> dict[str, Any]:
    return {POOL_KEY: pool, **extra}


async def run_jobs_once(app: procrastinate.App, context: dict[str, Any]) -> None:
    """Process every job that is due now, then return (tests and `make` helpers)."""
    await app.run_worker_async(
        wait=False, install_signal_handlers=False, additional_context=context
    )


def job_settings_summary(settings: Settings) -> str:
    return (
        f"max {settings.job_max_attempts} attempts,"
        f" backoff {settings.job_retry_base_seconds}s x 2^n"
    )


def is_last_attempt(context: procrastinate.JobContext) -> bool:
    return context.job.attempts + 1 >= get_settings().job_max_attempts
