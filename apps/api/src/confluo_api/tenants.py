"""Tenants the signed-in user belongs to, and the first tenant-scoped resource."""

from datetime import datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, HTTPException, Request, status
from psycopg import errors
from psycopg.rows import class_row
from pydantic import BaseModel, StringConstraints

from confluo_core.auth import CurrentUser
from confluo_core.deps import Pool, client_ip
from confluo_core.tenancy import user_transaction

router = APIRouter()

Name = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=120)]
Slug = Annotated[str, StringConstraints(pattern=r"^[a-z0-9](-?[a-z0-9])+$", max_length=40)]


class TenantOut(BaseModel):
    id: UUID
    name: str
    slug: str
    role: str


class PendingInvitation(BaseModel):
    id: UUID
    tenant_id: UUID
    tenant_name: str
    role: str
    expires_at: datetime


class Me(BaseModel):
    id: UUID
    email: str | None
    tenants: list[TenantOut]
    invitations: list[PendingInvitation]


class TenantIn(BaseModel):
    name: Name
    slug: Slug


@router.get("/api/me", tags=["auth"], operation_id="getMe")
async def me(user: CurrentUser, pool: Pool, request: Request) -> Me:
    async with user_transaction(pool, user.id, user.email, client_ip(request)) as conn:
        cur = conn.cursor(row_factory=class_row(TenantOut))
        await cur.execute(
            "select t.id, t.name, t.slug, m.role from tenant t"
            " join tenant_member m on m.tenant_id = t.id"
            " where m.user_id = %s and m.status = 'active' order by t.name",
            (user.id,),
        )
        tenants = await cur.fetchall()
        inv = conn.cursor(row_factory=class_row(PendingInvitation))
        await inv.execute("select * from app.my_invitations()")
        invitations = await inv.fetchall()
    return Me(id=user.id, email=user.email, tenants=tenants, invitations=invitations)


@router.post(
    "/api/me/invitations/{invitation_id}/accept",
    tags=["auth"],
    operation_id="acceptInvitation",
)
async def accept_invitation(
    invitation_id: UUID, user: CurrentUser, pool: Pool, request: Request
) -> TenantOut:
    """Join the tenant of an invitation addressed to the signed-in user's email."""
    async with user_transaction(pool, user.id, user.email, client_ip(request)) as conn:
        try:
            async with conn.transaction():
                cur = await conn.execute("select app.accept_invitation(%s)", (invitation_id,))
        except errors.NoDataFound:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "No such invitation") from None
        row = await cur.fetchone()
        assert row is not None
        joined = conn.cursor(row_factory=class_row(TenantOut))
        await joined.execute(
            "select t.id, t.name, t.slug, m.role from tenant t"
            " join tenant_member m on m.tenant_id = t.id where t.id = %s and m.user_id = %s",
            (row[0], user.id),
        )
        tenant = await joined.fetchone()
    assert tenant is not None
    return tenant


@router.post(
    "/api/tenants",
    tags=["tenants"],
    operation_id="createTenant",
    status_code=status.HTTP_201_CREATED,
)
async def create_tenant(
    body: TenantIn, user: CurrentUser, pool: Pool, request: Request
) -> TenantOut:
    """Create a business; the caller becomes its owner."""
    name = user.claims.get("user_metadata", {}).get("name")
    async with user_transaction(pool, user.id, user.email, client_ip(request)) as conn:
        await conn.execute(
            "insert into app_user (id, email, name) values (%s, %s, %s)"
            " on conflict (id) do update set email = excluded.email",
            (user.id, user.email or "", name),
        )
        try:
            async with conn.transaction():
                cur = await conn.execute("select app.create_tenant(%s, %s)", (body.name, body.slug))
        except errors.UniqueViolation:
            raise HTTPException(status.HTTP_409_CONFLICT, "That slug is taken") from None
        row = await cur.fetchone()
        assert row is not None
    return TenantOut(id=row[0], name=body.name, slug=body.slug, role="owner")
