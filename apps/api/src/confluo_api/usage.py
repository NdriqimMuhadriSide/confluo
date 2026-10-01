"""AI usage and estimated cost for the current tenant."""

from datetime import date
from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel

from confluo_core.deps import TenantContext, requires

router = APIRouter()

ViewUsage = Annotated[TenantContext, Depends(requires("core.usage.view"))]


class UsageRow(BaseModel):
    day: date
    tier: str
    model: str
    purpose: str
    calls: int
    input_tokens: int
    output_tokens: int
    cache_read_tokens: int
    cost_usd: Decimal | None  # None when the model has no price on file


class UsageSummary(BaseModel):
    days: int
    total_cost_usd: Decimal
    rows: list[UsageRow]


@router.get("/api/usage", tags=["usage"], operation_id="getUsage")
async def usage(tenant: ViewUsage, days: int = Query(30, ge=1, le=366)) -> UsageSummary:
    cur = await tenant.conn.execute(
        "select (created_at at time zone 'UTC')::date as day, tier, model, purpose,"
        " count(*), sum(input_tokens), sum(output_tokens), sum(cache_read_tokens),"
        " case when bool_and(cost_usd is not null) then sum(cost_usd) end"
        " from llm_usage where tenant_id = %s and created_at > now() - make_interval(days => %s)"
        " group by 1, 2, 3, 4 order by 1 desc, 2, 3, 4",
        (tenant.tenant_id, days),
    )
    rows = [
        UsageRow(
            day=r[0],
            tier=r[1],
            model=r[2],
            purpose=r[3],
            calls=r[4],
            input_tokens=r[5],
            output_tokens=r[6],
            cache_read_tokens=r[7],
            cost_usd=r[8],
        )
        for r in await cur.fetchall()
    ]
    total = sum((r.cost_usd for r in rows if r.cost_usd is not None), Decimal(0))
    return UsageSummary(days=days, total_cost_usd=total, rows=rows)
