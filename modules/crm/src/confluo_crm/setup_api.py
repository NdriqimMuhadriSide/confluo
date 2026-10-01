"""Business setup for booking: services, resources (staff, rooms), weekly schedules,
days off, and the resulting free time. Mounted under /api/crm."""

from datetime import date, datetime, time, timedelta
from typing import Annotated, Any, Literal
from uuid import UUID
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from psycopg import errors
from psycopg.types.json import Jsonb
from pydantic import BaseModel, Field, StringConstraints, model_validator

from confluo_core.deps import Tenant, TenantContext, requires
from confluo_core.schedule import Interval, check_intervals
from confluo_crm.availability import load_free_intervals

router = APIRouter()

Manage = Annotated[TenantContext, Depends(requires("crm.settings.manage"))]
Text = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]
LANGS = {"en", "nl", "fr", "de", "sq"}


def _langs(value: dict[str, str]) -> dict[str, str]:
    unknown = set(value) - LANGS
    if unknown:
        raise ValueError(f"unknown languages: {sorted(unknown)}")
    return value


# --- Services -------------------------------------------------------------------------


class ServiceField(BaseModel):
    field_id: UUID
    required: bool = True


class ServiceIn(BaseModel):
    name_i18n: dict[str, Text] = Field(min_length=1)
    description_i18n: dict[str, str] = {}
    duration_min: int = Field(ge=5, le=1440)
    buffer_before_min: int = Field(0, ge=0, le=240)
    buffer_after_min: int = Field(0, ge=0, le=240)
    price_cents: int | None = Field(None, ge=0)
    active: bool = True
    position: int = 0
    fields: list[ServiceField] = []
    resource_ids: list[UUID] = []

    @model_validator(mode="after")
    def _check(self) -> "ServiceIn":
        _langs(self.name_i18n)
        _langs(self.description_i18n)
        if len({f.field_id for f in self.fields}) != len(self.fields):
            raise ValueError("a field is listed twice")
        return self


class ServiceOut(ServiceIn):
    id: UUID


SERVICE_COLUMNS = (
    "s.id, s.name_i18n, s.description_i18n, s.duration_min, s.buffer_before_min,"
    " s.buffer_after_min, s.price_cents, s.active, s.position,"
    " coalesce((select jsonb_agg(jsonb_build_object('field_id', f.field_definition_id,"
    " 'required', f.required) order by f.position) from crm_service_field f"
    " where f.service_id = s.id), '[]'),"
    " coalesce((select jsonb_agg(r.resource_id) from crm_service_resource r"
    " where r.service_id = s.id), '[]')"
)


def _service(r: Any) -> ServiceOut:
    return ServiceOut(
        id=r[0],
        name_i18n=r[1],
        description_i18n=r[2],
        duration_min=r[3],
        buffer_before_min=r[4],
        buffer_after_min=r[5],
        price_cents=r[6],
        active=r[7],
        position=r[8],
        fields=r[9],
        resource_ids=r[10],
    )


async def _get_service(tenant: TenantContext, service_id: UUID) -> ServiceOut:
    cur = await tenant.conn.execute(
        f"select {SERVICE_COLUMNS} from crm_service s where s.id = %s and s.tenant_id = %s",
        (service_id, tenant.tenant_id),
    )
    row = await cur.fetchone()
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such service")
    return _service(row)


async def _set_links(tenant: TenantContext, service_id: UUID, body: ServiceIn) -> None:
    """Replace the service's fields and resources. Composite foreign keys reject ids
    from other tenants; we turn that into a 422."""
    conn = tenant.conn
    await conn.execute("delete from crm_service_field where service_id = %s", (service_id,))
    await conn.execute("delete from crm_service_resource where service_id = %s", (service_id,))
    try:
        async with conn.transaction():
            for pos, f in enumerate(body.fields):
                await conn.execute(
                    "insert into crm_service_field"
                    " (service_id, field_definition_id, required, position)"
                    " values (%s, %s, %s, %s)",
                    (service_id, f.field_id, f.required, pos),
                )
            for rid in body.resource_ids:
                await conn.execute(
                    "insert into crm_service_resource (service_id, resource_id) values (%s, %s)",
                    (service_id, rid),
                )
    except errors.ForeignKeyViolation:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT, "Unknown field or resource"
        ) from None


@router.get("/services", operation_id="listServices")
async def list_services(tenant: Tenant) -> list[ServiceOut]:
    cur = await tenant.conn.execute(
        f"select {SERVICE_COLUMNS} from crm_service s where s.tenant_id = %s"
        " order by s.position, s.name_i18n::text",
        (tenant.tenant_id,),
    )
    return [_service(r) for r in await cur.fetchall()]


@router.post("/services", operation_id="createService", status_code=status.HTTP_201_CREATED)
async def create_service(body: ServiceIn, tenant: Manage) -> ServiceOut:
    cur = await tenant.conn.execute(
        "insert into crm_service (name_i18n, description_i18n, duration_min, buffer_before_min,"
        " buffer_after_min, price_cents, active, position)"
        " values (%s, %s, %s, %s, %s, %s, %s, %s) returning id",
        (
            Jsonb(body.name_i18n),
            Jsonb(body.description_i18n),
            body.duration_min,
            body.buffer_before_min,
            body.buffer_after_min,
            body.price_cents,
            body.active,
            body.position,
        ),
    )
    row = await cur.fetchone()
    assert row is not None
    await _set_links(tenant, row[0], body)
    return await _get_service(tenant, row[0])


@router.put("/services/{service_id}", operation_id="updateService")
async def update_service(service_id: UUID, body: ServiceIn, tenant: Manage) -> ServiceOut:
    cur = await tenant.conn.execute(
        "update crm_service set name_i18n = %s, description_i18n = %s, duration_min = %s,"
        " buffer_before_min = %s, buffer_after_min = %s, price_cents = %s, active = %s,"
        " position = %s where id = %s and tenant_id = %s",
        (
            Jsonb(body.name_i18n),
            Jsonb(body.description_i18n),
            body.duration_min,
            body.buffer_before_min,
            body.buffer_after_min,
            body.price_cents,
            body.active,
            body.position,
            service_id,
            tenant.tenant_id,
        ),
    )
    if cur.rowcount == 0:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such service")
    await _set_links(tenant, service_id, body)
    return await _get_service(tenant, service_id)


@router.delete(
    "/services/{service_id}", operation_id="deleteService", status_code=status.HTTP_204_NO_CONTENT
)
async def delete_service(service_id: UUID, tenant: Manage) -> Response:
    try:
        async with tenant.conn.transaction():
            cur = await tenant.conn.execute(
                "delete from crm_service where id = %s and tenant_id = %s",
                (service_id, tenant.tenant_id),
            )
    except errors.ForeignKeyViolation:
        raise HTTPException(
            status.HTTP_409_CONFLICT, "The service has appointments; deactivate it instead"
        ) from None
    if cur.rowcount == 0:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such service")
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# --- Resources --------------------------------------------------------------------------


class ResourceIn(BaseModel):
    kind: Literal["staff", "room", "equipment"]
    name: Text
    location_id: UUID | None = None
    member_id: UUID | None = None  # the team member this staff resource is
    active: bool = True


class ResourceOut(ResourceIn):
    id: UUID


RESOURCE_COLUMNS = "id, kind, name, location_id, member_id, active"


def _resource(r: Any) -> ResourceOut:
    return ResourceOut(id=r[0], kind=r[1], name=r[2], location_id=r[3], member_id=r[4], active=r[5])


async def _check_member(tenant: TenantContext, member_id: UUID | None) -> None:
    if member_id is None:
        return
    cur = await tenant.conn.execute(
        "select 1 from tenant_member where tenant_id = %s and user_id = %s",
        (tenant.tenant_id, member_id),
    )
    if await cur.fetchone() is None:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Not a member of this business")


async def _resource_or_404(tenant: TenantContext, resource_id: UUID) -> None:
    cur = await tenant.conn.execute(
        "select 1 from crm_resource where id = %s and tenant_id = %s",
        (resource_id, tenant.tenant_id),
    )
    if await cur.fetchone() is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such resource")


@router.get("/resources", operation_id="listResources")
async def list_resources(tenant: Tenant) -> list[ResourceOut]:
    cur = await tenant.conn.execute(
        f"select {RESOURCE_COLUMNS} from crm_resource where tenant_id = %s order by kind, name",
        (tenant.tenant_id,),
    )
    return [_resource(r) for r in await cur.fetchall()]


@router.post("/resources", operation_id="createResource", status_code=status.HTTP_201_CREATED)
async def create_resource(body: ResourceIn, tenant: Manage) -> ResourceOut:
    await _check_member(tenant, body.member_id)
    try:
        async with tenant.conn.transaction():
            cur = await tenant.conn.execute(
                "insert into crm_resource (kind, name, location_id, member_id, active)"
                f" values (%s, %s, %s, %s, %s) returning {RESOURCE_COLUMNS}",
                (body.kind, body.name, body.location_id, body.member_id, body.active),
            )
    except errors.ForeignKeyViolation:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Unknown location") from None
    return _resource(await cur.fetchone())


@router.put("/resources/{resource_id}", operation_id="updateResource")
async def update_resource(resource_id: UUID, body: ResourceIn, tenant: Manage) -> ResourceOut:
    await _check_member(tenant, body.member_id)
    try:
        async with tenant.conn.transaction():
            cur = await tenant.conn.execute(
                "update crm_resource set kind = %s, name = %s, location_id = %s, member_id = %s,"
                f" active = %s where id = %s and tenant_id = %s returning {RESOURCE_COLUMNS}",
                (
                    body.kind,
                    body.name,
                    body.location_id,
                    body.member_id,
                    body.active,
                    resource_id,
                    tenant.tenant_id,
                ),
            )
    except errors.ForeignKeyViolation:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Unknown location") from None
    row = await cur.fetchone()
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such resource")
    return _resource(row)


# --- Weekly schedule ----------------------------------------------------------------------


class WeeklyRule(BaseModel):
    weekday: int = Field(ge=1, le=7)  # 1 = Monday
    start: time
    end: time
    valid_from: date | None = None
    valid_to: date | None = None


class Schedule(BaseModel):
    rules: list[WeeklyRule]

    @model_validator(mode="after")
    def _no_overlap(self) -> "Schedule":
        for day in range(1, 8):
            check_intervals(
                [Interval(start=r.start, end=r.end) for r in self.rules if r.weekday == day]
            )
        return self


@router.get("/resources/{resource_id}/schedule", operation_id="getSchedule")
async def get_schedule(resource_id: UUID, tenant: Tenant) -> Schedule:
    await _resource_or_404(tenant, resource_id)
    cur = await tenant.conn.execute(
        "select weekday, start_time, end_time, valid_from, valid_to from crm_availability_rule"
        " where resource_id = %s order by weekday, start_time",
        (resource_id,),
    )
    return Schedule(
        rules=[
            WeeklyRule(weekday=r[0], start=r[1], end=r[2], valid_from=r[3], valid_to=r[4])
            for r in await cur.fetchall()
        ]
    )


@router.put("/resources/{resource_id}/schedule", operation_id="setSchedule")
async def set_schedule(resource_id: UUID, body: Schedule, tenant: Manage) -> Schedule:
    """Replace the whole weekly schedule."""
    await _resource_or_404(tenant, resource_id)
    await tenant.conn.execute(
        "delete from crm_availability_rule where resource_id = %s", (resource_id,)
    )
    for r in body.rules:
        await tenant.conn.execute(
            "insert into crm_availability_rule"
            " (resource_id, weekday, start_time, end_time, valid_from, valid_to)"
            " values (%s, %s, %s, %s, %s, %s)",
            (resource_id, r.weekday, r.start, r.end, r.valid_from, r.valid_to),
        )
    return await get_schedule(resource_id, tenant)


# --- Days off and extra hours -----------------------------------------------------------------


class ExceptionIn(BaseModel):
    """Whole days (first_day..last_day, local dates) or a time range on one day."""

    kind: Literal["off", "extra"] = "off"
    first_day: date
    last_day: date | None = None
    start: time | None = None  # with `end`: only this part of first_day
    end: time | None = None
    reason: str | None = Field(None, max_length=200)

    @model_validator(mode="after")
    def _check(self) -> "ExceptionIn":
        if self.last_day and self.last_day < self.first_day:
            raise ValueError("last_day is before first_day")
        if (self.start is None) != (self.end is None):
            raise ValueError("give both start and end, or neither")
        if self.start and self.end and self.start >= self.end:
            raise ValueError("start must be before end")
        if self.kind == "extra" and self.start is None:
            raise ValueError("extra hours need a start and end time")
        return self


class ExceptionOut(BaseModel):
    id: UUID
    kind: str
    start: datetime
    end: datetime
    reason: str | None


async def _resource_tz(tenant: TenantContext, resource_id: UUID) -> ZoneInfo:
    cur = await tenant.conn.execute(
        "select coalesce(l.timezone, t.timezone) from crm_resource r"
        " join tenant t on t.id = r.tenant_id left join location l on l.id = r.location_id"
        " where r.id = %s and r.tenant_id = %s",
        (resource_id, tenant.tenant_id),
    )
    row = await cur.fetchone()
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such resource")
    return ZoneInfo(row[0])


@router.get("/resources/{resource_id}/exceptions", operation_id="listExceptions")
async def list_exceptions(resource_id: UUID, tenant: Tenant) -> list[ExceptionOut]:
    await _resource_or_404(tenant, resource_id)
    cur = await tenant.conn.execute(
        "select id, kind, lower(during), upper(during), reason from crm_availability_exception"
        " where resource_id = %s and upper(during) > now() order by lower(during)",
        (resource_id,),
    )
    return [
        ExceptionOut(id=r[0], kind=r[1], start=r[2], end=r[3], reason=r[4])
        for r in await cur.fetchall()
    ]


@router.post(
    "/resources/{resource_id}/exceptions",
    operation_id="addException",
    status_code=status.HTTP_201_CREATED,
)
async def add_exception(resource_id: UUID, body: ExceptionIn, tenant: Manage) -> ExceptionOut:
    tz = await _resource_tz(tenant, resource_id)
    if body.start and body.end:
        start = datetime.combine(body.first_day, body.start, tz)
        end = datetime.combine(body.first_day, body.end, tz)
    else:
        start = datetime.combine(body.first_day, time(0), tz)
        end = datetime.combine((body.last_day or body.first_day) + timedelta(days=1), time(0), tz)
    cur = await tenant.conn.execute(
        "insert into crm_availability_exception (resource_id, during, kind, reason)"
        " values (%s, tstzrange(%s, %s), %s, %s) returning id",
        (resource_id, start, end, body.kind, body.reason),
    )
    row = await cur.fetchone()
    assert row is not None
    return ExceptionOut(id=row[0], kind=body.kind, start=start, end=end, reason=body.reason)


@router.delete(
    "/resources/{resource_id}/exceptions/{exception_id}",
    operation_id="deleteException",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_exception(resource_id: UUID, exception_id: UUID, tenant: Manage) -> Response:
    cur = await tenant.conn.execute(
        "delete from crm_availability_exception where id = %s and resource_id = %s"
        " and tenant_id = %s",
        (exception_id, resource_id, tenant.tenant_id),
    )
    if cur.rowcount == 0:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such exception")
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# --- Free time ----------------------------------------------------------------------------


class FreeSpan(BaseModel):
    start: datetime
    end: datetime


class FreeTime(BaseModel):
    timezone: str
    spans: list[FreeSpan]


@router.get("/resources/{resource_id}/free", operation_id="getFreeTime")
async def free_time(
    resource_id: UUID,
    tenant: Tenant,
    start: date,
    days: int = Query(7, ge=1, le=62),
) -> FreeTime:
    """Working time minus days off, appointments and external busy time, in local time."""
    await _resource_or_404(tenant, resource_id)
    tz, spans = await load_free_intervals(tenant.conn, resource_id, start, days)
    return FreeTime(
        timezone=str(tz),
        spans=[FreeSpan(start=s.start.astimezone(tz), end=s.end.astimezone(tz)) for s in spans],
    )
