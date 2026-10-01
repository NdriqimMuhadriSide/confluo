"""System health: a tenant's failed background jobs, with manual retry."""

from datetime import UTC, datetime
from typing import Annotated

import procrastinate
from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel

from confluo_core.deps import TenantContext, requires

router = APIRouter()

ManageSystem = Annotated[TenantContext, Depends(requires("core.system.manage"))]


class FailedJob(BaseModel):
    job_id: int
    task_name: str
    attempts: int
    error: str
    failed_at: datetime
    status: str


@router.get("/api/system/jobs", tags=["system"], operation_id="listFailedJobs")
async def failed_jobs(tenant: ManageSystem) -> list[FailedJob]:
    """Jobs of this tenant that used up their retries (dead letters)."""
    # job_error is tenant-isolated by RLS; the join only adds the live status.
    cur = await tenant.conn.execute(
        "select e.job_id, e.task_name, e.attempts, e.error, e.failed_at, j.status::text"
        " from job_error e join procrastinate.procrastinate_jobs j on j.id = e.job_id"
        " where e.tenant_id = %s and j.status = 'failed'"
        " order by e.failed_at desc limit 200",
        (tenant.tenant_id,),
    )
    return [
        FailedJob(
            job_id=r[0], task_name=r[1], attempts=r[2], error=r[3], failed_at=r[4], status=r[5]
        )
        for r in await cur.fetchall()
    ]


@router.post(
    "/api/system/jobs/{job_id}/retry", tags=["system"], operation_id="retryJob", status_code=202
)
async def retry_job(job_id: int, tenant: ManageSystem, request: Request) -> dict[str, str]:
    cur = await tenant.conn.execute(
        "select 1 from job_error e join procrastinate.procrastinate_jobs j on j.id = e.job_id"
        " where e.job_id = %s and e.tenant_id = %s and j.status = 'failed'",
        (job_id, tenant.tenant_id),
    )
    if await cur.fetchone() is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such failed job")
    job_app: procrastinate.App = request.app.state.job_app
    await job_app.job_manager.retry_job_by_id_async(job_id, retry_at=datetime.now(UTC))
    await tenant.conn.execute(
        "select app.audit_event('retry', 'job', %s, '{}'::jsonb)", (str(job_id),)
    )
    return {"status": "queued"}
