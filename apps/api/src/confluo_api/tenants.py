"""Tenants the signed-in user belongs to, and the first tenant-scoped resource."""

from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, HTTPException, Request, status
from psycopg import errors
from psycopg.rows import class_row
from pydantic import BaseModel, StringConstraints

from confluo_core.auth import CurrentUser
from confluo_core.deps import Pool, client_ip
from confluo_core.modules import ConfluoModule
from confluo_core.tenancy import require_membership, user_transaction

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
    # Optional industry preset (GET /api/presets) and the language for its texts.
    preset: str | None = None
    language: Literal["en", "nl", "fr", "de", "sq"] = "en"


class PresetOut(BaseModel):
    key: str
    name_i18n: dict[str, str]
    description_i18n: dict[str, str]


def _catalog(request: Request) -> dict[str, list[ConfluoModule]]:
    """Preset key -> the modules that set something up for it."""
    modules: dict[str, ConfluoModule] = request.app.state.modules
    found: dict[str, list[ConfluoModule]] = {}
    for m in modules.values():
        for p in m.presets:
            found.setdefault(p.key, []).append(m)
    return found


@router.get("/api/presets", tags=["tenants"], operation_id="listPresets")
async def list_presets(request: Request, user: CurrentUser) -> list[PresetOut]:
    modules: dict[str, ConfluoModule] = request.app.state.modules
    seen: dict[str, PresetOut] = {}
    for m in modules.values():
        for p in m.presets:
            seen.setdefault(
                p.key,
                PresetOut(key=p.key, name_i18n=p.name_i18n, description_i18n=p.description_i18n),
            )
    return list(seen.values())


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
    """Create a business, optionally from a preset; the caller becomes its owner.
    Business and preset data are created in one transaction."""
    catalog = _catalog(request)
    if body.preset is not None and body.preset not in catalog:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Unknown preset")
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
        tenant_id = row[0]
        await require_membership(conn, tenant_id, user.id)  # enter the new tenant
        await conn.execute(
            "update tenant set default_locale = %s where id = %s", (body.language, tenant_id)
        )
        if body.preset is not None:
            cur = await conn.execute("select timezone from tenant where id = %s", (tenant_id,))
            tz_row = await cur.fetchone()
            timezone = tz_row[0] if tz_row else "Europe/Brussels"
            for module in catalog[body.preset]:
                await module.apply_preset(conn, body.preset, body.language, timezone)
    return TenantOut(id=tenant_id, name=body.name, slug=body.slug, role="owner")
