"""Read access to the audit log and the AI action log."""

from datetime import datetime
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from psycopg.rows import class_row
from pydantic import BaseModel

from confluo_core.deps import TenantContext, requires

router = APIRouter()

ViewAudit = Annotated[TenantContext, Depends(requires("core.audit.view"))]
ViewAITrace = Annotated[TenantContext, Depends(requires("core.ai_trace.view"))]


class AuditEntry(BaseModel):
    id: int
    actor_type: str
    actor_id: UUID | None
    actor_email: str | None
    action: str
    entity: str
    entity_id: str | None
    diff: dict[str, Any]
    ip: str | None
    at: datetime


class AIAction(BaseModel):
    id: UUID
    run_id: UUID
    conversation_id: UUID | None
    node: str
    tool: str | None
    input: dict[str, Any]
    output: dict[str, Any]
    rationale: str | None
    sources: list[Any]
    model: str | None
    input_tokens: int | None
    output_tokens: int | None
    latency_ms: int | None
    confidence: float | None
    outcome: str | None
    approval_status: str
    created_at: datetime


@router.get("/api/audit", tags=["audit"], operation_id="listAudit")
async def list_audit(
    tenant: ViewAudit,
    entity: str | None = None,
    entity_id: str | None = None,
    before: int | None = Query(None, description="Return entries with a smaller id (paging)."),
    limit: int = Query(50, ge=1, le=200),
) -> list[AuditEntry]:
    cur = tenant.conn.cursor(row_factory=class_row(AuditEntry))
    await cur.execute(
        "select a.id, a.actor_type, a.actor_id, u.email as actor_email, a.action, a.entity,"
        " a.entity_id, a.diff, host(a.ip) as ip, a.at"
        " from audit_log a left join app_user u on u.id = a.actor_id"
        " where a.tenant_id = %(t)s"
        " and (%(entity)s::text is null or a.entity = %(entity)s)"
        " and (%(entity_id)s::text is null or a.entity_id = %(entity_id)s)"
        " and (%(before)s::bigint is null or a.id < %(before)s)"
        " order by a.id desc limit %(limit)s",
        {
            "t": tenant.tenant_id,
            "entity": entity,
            "entity_id": entity_id,
            "before": before,
            "limit": limit,
        },
    )
    return await cur.fetchall()


@router.get("/api/ai-actions", tags=["audit"], operation_id="listAIActions")
async def list_ai_actions(
    tenant: ViewAITrace,
    run_id: UUID | None = None,
    conversation_id: UUID | None = None,
    limit: int = Query(100, ge=1, le=500),
) -> list[AIAction]:
    if run_id is None and conversation_id is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Pass run_id or conversation_id")
    cur = tenant.conn.cursor(row_factory=class_row(AIAction))
    await cur.execute(
        "select id, run_id, conversation_id, node, tool, input, output, rationale, sources,"
        " model, input_tokens, output_tokens, latency_ms, confidence, outcome,"
        " approval_status, created_at from ai_action"
        " where tenant_id = %(t)s"
        " and (%(run)s::uuid is null or run_id = %(run)s)"
        " and (%(conv)s::uuid is null or conversation_id = %(conv)s)"
        " order by created_at limit %(limit)s",
        {"t": tenant.tenant_id, "run": run_id, "conv": conversation_id, "limit": limit},
    )
    return await cur.fetchall()
