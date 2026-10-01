"""Tenants the signed-in user belongs to, and the first tenant-scoped resource."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, HTTPException, status
from psycopg import errors
from psycopg.rows import class_row
from pydantic import BaseModel, Field, StringConstraints

from confluo_core.auth import CurrentUser
from confluo_core.deps import Pool, Tenant
from confluo_core.tenancy import user_transaction

router = APIRouter()

Name = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=120)]
Slug = Annotated[str, StringConstraints(pattern=r"^[a-z0-9](-?[a-z0-9])+$", max_length=40)]


class TenantOut(BaseModel):
    id: UUID
    name: str
    slug: str
    role: str


class Me(BaseModel):
    id: UUID
    email: str | None
    tenants: list[TenantOut]


class TenantIn(BaseModel):
    name: Name
    slug: Slug


class LocationIn(BaseModel):
    name: Name
    address: str | None = Field(default=None, max_length=300)
    timezone: str | None = Field(default=None, max_length=64)


class LocationOut(BaseModel):
    id: UUID
    name: str
    address: str | None
    timezone: str | None


@router.get("/api/me", tags=["auth"], operation_id="getMe")
async def me(user: CurrentUser, pool: Pool) -> Me:
    async with user_transaction(pool, user.id) as conn:
        cur = conn.cursor(row_factory=class_row(TenantOut))
        await cur.execute(
            "select t.id, t.name, t.slug, m.role from tenant t"
            " join tenant_member m on m.tenant_id = t.id"
            " where m.user_id = %s and m.status = 'active' order by t.name",
            (user.id,),
        )
        tenants = await cur.fetchall()
    return Me(id=user.id, email=user.email, tenants=tenants)


@router.post(
    "/api/tenants",
    tags=["tenants"],
    operation_id="createTenant",
    status_code=status.HTTP_201_CREATED,
)
async def create_tenant(body: TenantIn, user: CurrentUser, pool: Pool) -> TenantOut:
    """Create a business; the caller becomes its owner."""
    name = user.claims.get("user_metadata", {}).get("name")
    async with user_transaction(pool, user.id) as conn:
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


@router.get("/api/locations", tags=["locations"], operation_id="listLocations")
async def list_locations(tenant: Tenant) -> list[LocationOut]:
    cur = tenant.conn.cursor(row_factory=class_row(LocationOut))
    await cur.execute("select id, name, address, timezone from location order by name")
    return await cur.fetchall()


@router.post(
    "/api/locations",
    tags=["locations"],
    operation_id="createLocation",
    status_code=status.HTTP_201_CREATED,
)
async def create_location(body: LocationIn, tenant: Tenant) -> LocationOut:
    # tenant_id defaults to app.current_tenant_id() in the database.
    cur = tenant.conn.cursor(row_factory=class_row(LocationOut))
    await cur.execute(
        "insert into location (name, address, timezone) values (%s, %s, %s)"
        " returning id, name, address, timezone",
        (body.name, body.address, body.timezone),
    )
    row = await cur.fetchone()
    assert row is not None
    return row
