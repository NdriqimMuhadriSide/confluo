"""Knowledge base editor API and search. Mounted under /api/crm."""

from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

import procrastinate
from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from pydantic import BaseModel, StringConstraints

from confluo_core.deps import LLM, Tenant, TenantContext, requires
from confluo_core.jobs import defer_in
from confluo_crm.knowledge import hybrid_search

router = APIRouter()

Edit = Annotated[TenantContext, Depends(requires("crm.kb.edit"))]
Kind = Literal["faq", "service", "price", "hours", "location", "policy", "free_text"]
Language = Literal["en", "nl", "fr", "de", "sq"]
Title = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]
Body = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=20_000)]
EMBED_TASK = "crm:embed_knowledge_item"


class ItemIn(BaseModel):
    kind: Kind
    title: Title
    body: Body
    language: Language


class ItemOut(ItemIn):
    id: UUID
    published: bool
    # "draft", "indexing" (published, waiting for the embedding job) or "live".
    status: Literal["draft", "indexing", "live"]
    chunks: int
    updated_at: datetime


COLUMNS = (
    "i.id, i.kind, i.title, i.body, i.language, i.published, i.embedded_at, i.updated_at,"
    " (select count(*) from crm_knowledge_chunk c where c.knowledge_item_id = i.id)"
)


def _out(r: tuple[object, ...]) -> ItemOut:
    published, embedded = bool(r[5]), r[6]
    state: Literal["draft", "indexing", "live"] = (
        "draft" if not published else ("live" if embedded else "indexing")
    )
    return ItemOut(
        id=r[0],
        kind=r[1],
        title=r[2],
        body=r[3],
        language=r[4],
        published=published,
        status=state,
        chunks=r[8],
        updated_at=r[7],
    )


async def _get(tenant: TenantContext, item_id: UUID) -> ItemOut:
    cur = await tenant.conn.execute(
        f"select {COLUMNS} from crm_knowledge_item i where i.id = %s and i.tenant_id = %s",
        (item_id, tenant.tenant_id),
    )
    row = await cur.fetchone()
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such item")
    return _out(row)


async def _reindex(tenant: TenantContext, request: Request, item_id: UUID) -> None:
    """Mark the item for (re)indexing and enqueue the job in this transaction."""
    await tenant.conn.execute(
        "update crm_knowledge_item set embedded_at = null where id = %s", (item_id,)
    )
    job_app: procrastinate.App = request.app.state.job_app
    await defer_in(
        tenant.conn,
        job_app.tasks[EMBED_TASK],
        tenant_id=str(tenant.tenant_id),
        item_id=str(item_id),
    )


@router.get("/knowledge", operation_id="listKnowledge")
async def list_items(tenant: Edit, language: Language | None = None) -> list[ItemOut]:
    cur = await tenant.conn.execute(
        f"select {COLUMNS} from crm_knowledge_item i where i.tenant_id = %(t)s"
        " and (%(l)s::text is null or i.language = %(l)s) order by i.kind, i.title",
        {"t": tenant.tenant_id, "l": language},
    )
    return [_out(r) for r in await cur.fetchall()]


@router.post("/knowledge", operation_id="createKnowledge", status_code=status.HTTP_201_CREATED)
async def create_item(body: ItemIn, tenant: Edit) -> ItemOut:
    cur = await tenant.conn.execute(
        "insert into crm_knowledge_item (kind, title, body, language) values (%s, %s, %s, %s)"
        " returning id",
        (body.kind, body.title, body.body, body.language),
    )
    row = await cur.fetchone()
    assert row is not None
    return await _get(tenant, row[0])


@router.put("/knowledge/{item_id}", operation_id="updateKnowledge")
async def update_item(item_id: UUID, body: ItemIn, tenant: Edit, request: Request) -> ItemOut:
    cur = await tenant.conn.execute(
        "update crm_knowledge_item set kind = %s, title = %s, body = %s, language = %s"
        " where id = %s and tenant_id = %s returning published",
        (body.kind, body.title, body.body, body.language, item_id, tenant.tenant_id),
    )
    row = await cur.fetchone()
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such item")
    if row[0]:  # editing live content re-indexes it
        await _reindex(tenant, request, item_id)
    return await _get(tenant, item_id)


@router.post("/knowledge/{item_id}/publish", operation_id="publishKnowledge")
async def publish(item_id: UUID, tenant: Edit, request: Request) -> ItemOut:
    cur = await tenant.conn.execute(
        "update crm_knowledge_item set published = true, published_at = now()"
        " where id = %s and tenant_id = %s",
        (item_id, tenant.tenant_id),
    )
    if cur.rowcount == 0:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such item")
    await _reindex(tenant, request, item_id)
    return await _get(tenant, item_id)


@router.post("/knowledge/{item_id}/unpublish", operation_id="unpublishKnowledge")
async def unpublish(item_id: UUID, tenant: Edit) -> ItemOut:
    cur = await tenant.conn.execute(
        "update crm_knowledge_item set published = false, embedded_at = null"
        " where id = %s and tenant_id = %s",
        (item_id, tenant.tenant_id),
    )
    if cur.rowcount == 0:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such item")
    await tenant.conn.execute(
        "delete from crm_knowledge_chunk where knowledge_item_id = %s", (item_id,)
    )
    return await _get(tenant, item_id)


@router.delete(
    "/knowledge/{item_id}", operation_id="deleteKnowledge", status_code=status.HTTP_204_NO_CONTENT
)
async def delete_item(item_id: UUID, tenant: Edit) -> Response:
    cur = await tenant.conn.execute(
        "delete from crm_knowledge_item where id = %s and tenant_id = %s",
        (item_id, tenant.tenant_id),
    )
    if cur.rowcount == 0:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such item")
    return Response(status_code=status.HTTP_204_NO_CONTENT)


class SearchHit(BaseModel):
    item_id: UUID
    kind: str
    title: str
    language: str
    content: str
    score: float
    vector_rank: int | None
    text_rank: int | None


@router.get("/knowledge/search", operation_id="searchKnowledge")
async def search(
    tenant: Tenant,
    llm: LLM,
    q: Annotated[str, Query(min_length=2, max_length=500)],
    language: Language | None = None,
    limit: int = Query(5, ge=1, le=20),
) -> list[SearchHit]:
    """Hybrid (vector + full-text) search over published knowledge, as the agent uses it."""
    hits = await hybrid_search(
        tenant.conn, llm, tenant.tenant_id, q, language=language, limit=limit
    )
    return [
        SearchHit(
            item_id=h.item_id,
            kind=h.kind,
            title=h.title,
            language=h.language,
            content=h.content,
            score=round(h.score, 5),
            vector_rank=h.vector_rank,
            text_rank=h.text_rank,
        )
        for h in hits
    ]
