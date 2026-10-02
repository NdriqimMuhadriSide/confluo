"""Availability engine: bookable start times for a service.

Deterministic: the free intervals of every resource that offers the service (working
time minus days off, appointments and external busy time, see availability.py), cut
into start times every STEP minutes where the service plus its buffers fits. The AI
only ever offers times from here, and booking re-checks them (the exclusion
constraint on crm_appointment makes a double booking impossible anyway).
"""

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from uuid import UUID
from zoneinfo import ZoneInfo

from psycopg import AsyncConnection

from confluo_crm.availability import Span, load_free_intervals

STEP = timedelta(minutes=15)
LEAD = timedelta(hours=1)  # earliest bookable time: now + LEAD


@dataclass(frozen=True)
class Service:
    id: UUID
    names: dict[str, str]
    duration: timedelta
    buffer_before: timedelta
    buffer_after: timedelta

    def name(self, language: str) -> str:
        return self.names.get(language) or self.names.get("en") or next(iter(self.names.values()))


@dataclass(frozen=True)
class Slot:
    start: datetime
    end: datetime
    resource_id: UUID
    resource_name: str


async def load_service(conn: AsyncConnection, service_id: UUID) -> Service | None:
    cur = await conn.execute(
        "select id, name_i18n, duration_min, buffer_before_min, buffer_after_min"
        " from crm_service where id = %s and active",
        (service_id,),
    )
    row = await cur.fetchone()
    if row is None:
        return None
    return Service(
        row[0],
        row[1],
        timedelta(minutes=row[2]),
        timedelta(minutes=row[3]),
        timedelta(minutes=row[4]),
    )


def _round_up(t: datetime) -> datetime:
    minutes = int(STEP.total_seconds() // 60)
    t = t.replace(second=0, microsecond=0)
    extra = (-t.minute) % minutes
    return t + timedelta(minutes=extra)


def cut(
    service: Service, free: list[Span], *, not_before: datetime
) -> list[tuple[datetime, datetime]]:
    """Start/end of every appointment of `service` that fits in `free` with its buffers."""
    out: list[tuple[datetime, datetime]] = []
    for span in free:
        start = _round_up(max(span.start + service.buffer_before, not_before))
        while start + service.duration + service.buffer_after <= span.end:
            out.append((start, start + service.duration))
            start += STEP
    return out


async def service_resources(conn: AsyncConnection, service_id: UUID) -> list[tuple[UUID, str]]:
    """Who can perform the service: its linked resources, or every active staff member
    when none are linked."""
    cur = await conn.execute(
        "select r.id, r.name from crm_service_resource sr join crm_resource r"
        " on r.id = sr.resource_id where sr.service_id = %s and r.active order by r.name",
        (service_id,),
    )
    rows = await cur.fetchall()
    if not rows:
        cur = await conn.execute(
            "select id, name from crm_resource where active and kind = 'staff' order by name"
        )
        rows = await cur.fetchall()
    return [(r[0], r[1]) for r in rows]


async def find_slots(
    conn: AsyncConnection,
    service: Service,
    start: date,
    days: int = 1,
    *,
    resource_id: UUID | None = None,
    now: datetime | None = None,
) -> tuple[ZoneInfo | None, list[Slot]]:
    """Bookable slots from `start` for `days` days, earliest first; at one start time
    the first free resource (by name) is chosen."""
    not_before = (now or datetime.now().astimezone()) + LEAD
    resources = await service_resources(conn, service.id)
    if resource_id is not None:
        resources = [r for r in resources if r[0] == resource_id]
    tz: ZoneInfo | None = None
    by_start: dict[datetime, Slot] = {}
    for rid, name in resources:
        tz, free = await load_free_intervals(conn, rid, start, days)
        for s, e in cut(service, free, not_before=not_before):
            by_start.setdefault(s, Slot(s, e, rid, name))
    return tz, [by_start[k] for k in sorted(by_start)]


def spread(slots: list[Slot], limit: int = 4, after: datetime | None = None) -> list[Slot]:
    """A few options spread over the day rather than four consecutive quarters."""
    if after is not None:
        slots = [s for s in slots if s.start >= after] or slots
    if len(slots) <= limit:
        return slots
    step = len(slots) / limit
    return [slots[int(i * step)] for i in range(limit)]
