"""Appointments for the dashboard calendar, plus free slots. Mounted under /api/crm.

    GET  /appointments?start=&days=&resource_id=     the calendar (+ the business timezone)
    POST /appointments                               staff books (confirmed)
    POST /appointments/{id}/approve | reject | cancel
    GET  /services/{id}/slots?start=&days=           bookable start times

Approving, rejecting or cancelling a booking made in a chat tells the customer in
that chat, in their language.
"""

from datetime import date, datetime, timedelta
from typing import Annotated, Literal
from uuid import UUID
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Query, status
from psycopg import AsyncConnection, errors
from pydantic import BaseModel, StringConstraints

from confluo_core.deps import Tenant, TenantContext, requires
from confluo_crm.channels.base import store_outbound
from confluo_crm.channels.registry import adapter_for
from confluo_crm.customer_messages import MESSAGES
from confluo_crm.slots import find_slots, load_service

router = APIRouter(tags=["crm-appointments"])

Book = Annotated[TenantContext, Depends(requires("crm.appointments.manage"))]
Name = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]
Status = Literal["pending_approval", "confirmed", "cancelled", "no_show", "completed"]


class AppointmentOut(BaseModel):
    id: UUID
    start: datetime
    end: datetime
    status: Status
    source: Literal["ai", "staff", "online"]
    service_id: UUID
    service_name: dict[str, str]
    resource_id: UUID
    resource_name: str
    customer_id: UUID
    customer_name: str | None
    conversation_id: UUID | None
    field_values: dict[str, object]


class CalendarOut(BaseModel):
    timezone: str  # the business's, for showing times
    appointments: list[AppointmentOut]


class AppointmentIn(BaseModel):
    service_id: UUID
    resource_id: UUID
    start: datetime  # without an offset: local time of the business
    customer_name: Name


class SlotOut(BaseModel):
    start: datetime
    end: datetime
    resource_id: UUID
    resource_name: str


COLUMNS = (
    "a.id, lower(a.during), upper(a.during), a.status, a.source, a.service_id, s.name_i18n,"
    " a.resource_id, r.name, a.customer_id, c.display_name, a.conversation_id, a.field_values"
    " from crm_appointment a join crm_service s on s.id = a.service_id"
    " join crm_resource r on r.id = a.resource_id join customer c on c.id = a.customer_id"
)


def _out(r: tuple[object, ...]) -> AppointmentOut:
    keys = list(AppointmentOut.model_fields)
    return AppointmentOut.model_validate(dict(zip(keys, r, strict=True)))


async def _get(conn: AsyncConnection, appointment_id: UUID) -> AppointmentOut:
    cur = await conn.execute(f"select {COLUMNS} where a.id = %s", (appointment_id,))
    row = await cur.fetchone()
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such appointment")
    return _out(row)


@router.get("/appointments", operation_id="listAppointments")
async def list_appointments(
    tenant: Tenant,
    start: date,
    days: int = Query(7, ge=1, le=62),
    resource_id: UUID | None = None,
) -> CalendarOut:
    cur = await tenant.conn.execute(
        "select t.timezone from tenant t where t.id = %s", (tenant.tenant_id,)
    )
    row = await cur.fetchone()
    tz = row[0] if row else "UTC"
    cur = await tenant.conn.execute(
        f"select {COLUMNS} where a.during && tstzrange("
        " (%s::date)::timestamp at time zone %s, (%s::date)::timestamp at time zone %s)"
        " and (%s::uuid is null or a.resource_id = %s) order by lower(a.during), r.name",
        (start, tz, start + timedelta(days=days), tz, resource_id, resource_id),
    )
    return CalendarOut(timezone=tz, appointments=[_out(r) for r in await cur.fetchall()])


@router.post("/appointments", operation_id="createAppointment", status_code=status.HTTP_201_CREATED)
async def create_appointment(body: AppointmentIn, tenant: Book) -> AppointmentOut:
    conn = tenant.conn
    service = await load_service(conn, body.service_id)
    if service is None:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Unknown service")
    if body.start.tzinfo is None:  # a wall-clock time: the business's timezone
        cur = await conn.execute("select timezone from tenant where id = %s", (tenant.tenant_id,))
        row = await cur.fetchone()
        body.start = body.start.replace(tzinfo=ZoneInfo(row[0] if row else "UTC"))
    try:
        async with conn.transaction():
            cur = await conn.execute(
                "insert into customer (display_name) values (%s) returning id",
                (body.customer_name,),
            )
            customer = await cur.fetchone()
            assert customer is not None
            cur = await conn.execute(
                "insert into crm_appointment (customer_id, service_id, resource_id, during,"
                " status, source) values (%s, %s, %s, tstzrange(%s, %s), 'confirmed', 'staff')"
                " returning id",
                (
                    customer[0],
                    body.service_id,
                    body.resource_id,
                    body.start,
                    body.start + service.duration,
                ),
            )
            created = await cur.fetchone()
    except errors.ExclusionViolation:
        raise HTTPException(status.HTTP_409_CONFLICT, "That time is already booked") from None
    except errors.ForeignKeyViolation:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Unknown resource") from None
    assert created is not None
    return await _get(conn, created[0])


async def _tell_customer(conn: AsyncConnection, a: AppointmentOut, event: str) -> None:
    if a.conversation_id is None:
        return
    cur = await conn.execute(
        "select c.channel, coalesce(c.language, 'en'), t.timezone from crm_conversation c"
        " join tenant t on t.id = c.tenant_id where c.id = %s",
        (a.conversation_id,),
    )
    row = await cur.fetchone()
    adapter = adapter_for(row[0]) if row else None
    if row is None or adapter is None:
        return
    _, language, tz = row
    local = a.start.astimezone(ZoneInfo(tz))
    template = MESSAGES[event].get(language, MESSAGES[event]["en"])
    service = a.service_name.get(language) or a.service_name.get("en") or ""
    text = template.format(service=service, when=local.strftime("%d/%m %H:%M"))
    outbound = await store_outbound(conn, a.conversation_id, text, sender_type="system")
    delivery = await adapter.send(conn, outbound)
    await conn.execute(
        "update crm_message set delivery_status = %s where id = %s",
        (delivery, outbound.message_id),
    )


async def _set_status(
    tenant: TenantContext, appointment_id: UUID, allowed: tuple[str, ...], new: str, event: str
) -> AppointmentOut:
    conn = tenant.conn
    current = await _get(conn, appointment_id)
    if current.status not in allowed:
        raise HTTPException(
            status.HTTP_409_CONFLICT, f"The appointment is {current.status.replace('_', ' ')}"
        )
    await conn.execute(
        "update crm_appointment set status = %s where id = %s", (new, appointment_id)
    )
    updated = await _get(conn, appointment_id)
    await _tell_customer(conn, updated, event)
    return updated


@router.post("/appointments/{appointment_id}/approve", operation_id="approveAppointment")
async def approve(appointment_id: UUID, tenant: Book) -> AppointmentOut:
    return await _set_status(
        tenant, appointment_id, ("pending_approval",), "confirmed", "confirmed"
    )


@router.post("/appointments/{appointment_id}/reject", operation_id="rejectAppointment")
async def reject(appointment_id: UUID, tenant: Book) -> AppointmentOut:
    return await _set_status(tenant, appointment_id, ("pending_approval",), "cancelled", "rejected")


@router.post("/appointments/{appointment_id}/cancel", operation_id="cancelAppointment")
async def cancel(appointment_id: UUID, tenant: Book) -> AppointmentOut:
    return await _set_status(
        tenant, appointment_id, ("pending_approval", "confirmed"), "cancelled", "cancelled"
    )


@router.get("/services/{service_id}/slots", operation_id="getServiceSlots")
async def slots(
    service_id: UUID,
    tenant: Tenant,
    start: date,
    days: int = Query(1, ge=1, le=31),
    resource_id: UUID | None = None,
) -> list[SlotOut]:
    service = await load_service(tenant.conn, service_id)
    if service is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such service")
    _, found = await find_slots(tenant.conn, service, start, days, resource_id=resource_id)
    return [
        SlotOut(start=s.start, end=s.end, resource_id=s.resource_id, resource_name=s.resource_name)
        for s in found
    ]
