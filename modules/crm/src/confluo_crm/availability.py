"""Free time per resource: the input of the availability engine (Phase 1).

Working time comes from the weekly rules (in the location's local time, so DST is
handled per day), plus `extra` exceptions, minus `off` exceptions, active
appointments and busy times synced from external calendars. The engine will cut
service-sized slots (with buffers) out of these intervals.
"""

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from uuid import UUID
from zoneinfo import ZoneInfo

from psycopg import AsyncConnection

ACTIVE_APPOINTMENT_STATUSES = ("pending_approval", "confirmed")


@dataclass(frozen=True)
class Rule:
    weekday: int  # 1 = Monday ... 7 = Sunday
    start: time
    end: time
    valid_from: date | None = None
    valid_to: date | None = None


@dataclass(frozen=True)
class Span:
    start: datetime
    end: datetime


def _merge(spans: Iterable[Span]) -> list[Span]:
    out: list[Span] = []
    for s in sorted(spans, key=lambda s: s.start):
        if out and s.start <= out[-1].end:
            if s.end > out[-1].end:
                out[-1] = Span(out[-1].start, s.end)
        else:
            out.append(s)
    return out


def _subtract(spans: list[Span], cuts: list[Span]) -> list[Span]:
    result: list[Span] = []
    for s in spans:
        pieces = [s]
        for c in cuts:
            nxt: list[Span] = []
            for p in pieces:
                if c.end <= p.start or c.start >= p.end:
                    nxt.append(p)
                    continue
                if c.start > p.start:
                    nxt.append(Span(p.start, c.start))
                if c.end < p.end:
                    nxt.append(Span(c.end, p.end))
            pieces = nxt
        result += pieces
    return result


def free_intervals(
    *,
    rules: list[Rule],
    extra: list[Span],
    off: list[Span],
    busy: list[Span],
    tz: ZoneInfo,
    start: date,
    days: int,
) -> list[Span]:
    """Free spans between `start` (local midnight) and `start + days`."""
    window = Span(
        datetime.combine(start, time(0), tz),
        datetime.combine(start + timedelta(days=days), time(0), tz),
    )
    working: list[Span] = []
    for offset in range(days):
        day = start + timedelta(days=offset)
        for r in rules:
            if r.weekday != day.isoweekday():
                continue
            if (r.valid_from and day < r.valid_from) or (r.valid_to and day > r.valid_to):
                continue
            working.append(
                Span(datetime.combine(day, r.start, tz), datetime.combine(day, r.end, tz))
            )
    working += [
        Span(max(e.start, window.start), min(e.end, window.end))
        for e in extra
        if e.end > window.start and e.start < window.end
    ]
    return _subtract(_merge(working), _merge(off + busy))


async def load_free_intervals(
    conn: AsyncConnection, resource_id: UUID, start: date, days: int
) -> tuple[ZoneInfo, list[Span]]:
    """Free spans of a resource in its location's timezone (tenant timezone otherwise)."""
    cur = await conn.execute(
        "select coalesce(l.timezone, t.timezone) from crm_resource r"
        " join tenant t on t.id = r.tenant_id left join location l on l.id = r.location_id"
        " where r.id = %s",
        (resource_id,),
    )
    row = await cur.fetchone()
    if row is None:
        raise LookupError(str(resource_id))
    tz = ZoneInfo(row[0])
    window_start = datetime.combine(start, time(0), tz)
    window_end = datetime.combine(start + timedelta(days=days), time(0), tz)

    cur = await conn.execute(
        "select weekday, start_time, end_time, valid_from, valid_to"
        " from crm_availability_rule where resource_id = %s",
        (resource_id,),
    )
    rules = [Rule(*r) for r in await cur.fetchall()]

    cur = await conn.execute(
        "select kind, lower(during), upper(during) from crm_availability_exception"
        " where resource_id = %s and during && tstzrange(%s, %s)",
        (resource_id, window_start, window_end),
    )
    extra: list[Span] = []
    off: list[Span] = []
    for kind, s, e in await cur.fetchall():
        (extra if kind == "extra" else off).append(Span(s, e))

    cur = await conn.execute(
        "select lower(during), upper(during) from crm_appointment"
        " where resource_id = %s and status = any(%s) and during && tstzrange(%s, %s)"
        " union all"
        " select lower(during), upper(during) from crm_external_busy"
        " where resource_id = %s and during && tstzrange(%s, %s)",
        (
            resource_id,
            list(ACTIVE_APPOINTMENT_STATUSES),
            window_start,
            window_end,
            resource_id,
            window_start,
            window_end,
        ),
    )
    busy = [Span(s, e) for s, e in await cur.fetchall()]
    return tz, free_intervals(
        rules=rules, extra=extra, off=off, busy=busy, tz=tz, start=start, days=days
    )
