"""Locations of the current tenant: address, timezone and opening hours."""

from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Response, status
from psycopg.types.json import Jsonb
from pydantic import BaseModel, Field, StringConstraints

from confluo_core.deps import Tenant, TenantContext, requires
from confluo_core.schedule import Timezone, WeeklyHours

router = APIRouter()

ManageLocations = Annotated[TenantContext, Depends(requires("core.locations.manage"))]
Name = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=120)]


class LocationIn(BaseModel):
    name: Name
    address: str | None = Field(default=None, max_length=300)
    timezone: Timezone = "Europe/Brussels"
    opening_hours: WeeklyHours = WeeklyHours()


class LocationPatch(BaseModel):
    name: Name | None = None
    address: str | None = Field(default=None, max_length=300)
    timezone: Timezone | None = None
    opening_hours: WeeklyHours | None = None


class LocationOut(BaseModel):
    id: UUID
    name: str
    address: str | None
    timezone: str | None
    opening_hours: WeeklyHours


COLUMNS = "id, name, address, timezone, opening_hours"


def _out(row: Any) -> LocationOut:
    return LocationOut(
        id=row[0],
        name=row[1],
        address=row[2],
        timezone=row[3],
        opening_hours=WeeklyHours.model_validate(row[4] or {}),
    )


@router.get("/api/locations", tags=["locations"], operation_id="listLocations")
async def list_locations(tenant: Tenant) -> list[LocationOut]:
    cur = await tenant.conn.execute(
        f"select {COLUMNS} from location where tenant_id = %s order by name", (tenant.tenant_id,)
    )
    return [_out(r) for r in await cur.fetchall()]


@router.post(
    "/api/locations",
    tags=["locations"],
    operation_id="createLocation",
    status_code=status.HTTP_201_CREATED,
)
async def create_location(body: LocationIn, tenant: ManageLocations) -> LocationOut:
    # tenant_id defaults to app.current_tenant_id() in the database.
    cur = await tenant.conn.execute(
        f"insert into location (name, address, timezone, opening_hours) values (%s, %s, %s, %s)"
        f" returning {COLUMNS}",
        (body.name, body.address, body.timezone, Jsonb(body.opening_hours.model_dump(mode="json"))),
    )
    return _out(await cur.fetchone())


@router.patch("/api/locations/{location_id}", tags=["locations"], operation_id="updateLocation")
async def update_location(
    location_id: UUID, body: LocationPatch, tenant: ManageLocations
) -> LocationOut:
    fields = body.model_dump(exclude_unset=True, mode="json")
    if "opening_hours" in fields:
        fields["opening_hours"] = Jsonb(fields["opening_hours"])
    if fields:
        sets = ", ".join(f"{k} = %({k})s" for k in fields)
        await tenant.conn.execute(
            f"update location set {sets} where id = %(id)s and tenant_id = %(t)s",
            {**fields, "id": location_id, "t": tenant.tenant_id},
        )
    cur = await tenant.conn.execute(
        f"select {COLUMNS} from location where id = %s and tenant_id = %s",
        (location_id, tenant.tenant_id),
    )
    row = await cur.fetchone()
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such location")
    return _out(row)


@router.delete(
    "/api/locations/{location_id}",
    tags=["locations"],
    operation_id="deleteLocation",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_location(location_id: UUID, tenant: ManageLocations) -> Response:
    cur = await tenant.conn.execute(
        "delete from location where id = %s and tenant_id = %s", (location_id, tenant.tenant_id)
    )
    if cur.rowcount == 0:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such location")
    return Response(status_code=status.HTTP_204_NO_CONTENT)
