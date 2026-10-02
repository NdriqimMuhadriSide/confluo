"""The booking flow of the intake graph.

Each customer turn, the model only *reads* the message (`BookingUpdate`: service, day,
time or option picked, name, field answers, yes/no). Code then decides the next step
and drafts the reply, so every offered time comes from the availability engine and
nothing is booked without an explicit "yes" to a summary:

    service? → day? → offer up to 4 free times → name and required fields → confirm → book

The state (`BookingState`) lives in the conversation's checkpoint between turns.
Drafts are English; the reply step rewrites them in the customer's language.
"""

from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from typing import Any, Literal
from uuid import UUID
from zoneinfo import ZoneInfo

from psycopg import AsyncConnection, errors
from psycopg.types.json import Jsonb
from pydantic import BaseModel, Field

from confluo_crm.slots import Slot, find_slots, load_service, spread

ACTIVE_STAGES = ("service", "date", "slot", "details", "confirm")
SEARCH_DAYS = 14  # when the asked day is full, look this far ahead


class FieldAnswer(BaseModel):
    key: str
    value: str


class BookingUpdate(BaseModel):
    service: str | None = Field(description="Exact name from SERVICES, if the customer chose one")
    date: str | None = Field(
        description="YYYY-MM-DD of the day the customer wants, resolved from TODAY"
    )
    time: str | None = Field(description="HH:MM the customer asked for or picked (24h)")
    option: int | None = Field(description="Number of the OPTIONS entry the customer picked")
    name: str | None = Field(description="The customer's name, if given")
    fields: list[FieldAnswer] = Field(description="Answers to FIELDS, by key")
    confirm: Literal["yes", "no"] | None = Field(
        description="Answer to the summary question when STAGE is confirm"
    )


@dataclass(frozen=True)
class FieldSpec:
    key: str
    label: str
    type: str


@dataclass
class Context:
    """What the flow needs to know about the business for one turn."""

    services: list[tuple[UUID, str]]  # (id, name in the customer's language)
    fields: list[FieldSpec]  # required booking fields of the chosen service
    tz: ZoneInfo
    today: date
    mode: str  # "auto" or "approval"


@dataclass
class Outcome:
    state: dict[str, Any]
    draft: str
    handoff: str | None = None  # a reason to hand over to staff instead
    appointment_id: UUID | None = None
    facts: list[str] = field(default_factory=list)


def is_active(booking: dict[str, Any] | None) -> bool:
    return booking is not None and booking.get("stage") in ACTIVE_STAGES


async def load_context(conn: AsyncConnection, language: str, service_id: str | None) -> Context:
    cur = await conn.execute(
        "select id, name_i18n from crm_service where active order by position, created_at"
    )
    services = []
    for sid, names in await cur.fetchall():
        services.append((sid, names.get(language) or names.get("en") or next(iter(names.values()))))
    fields: list[FieldSpec] = []
    if service_id:
        cur = await conn.execute(
            "select d.key, d.label_i18n, d.type from crm_service_field f"
            " join field_definition d on d.id = f.field_definition_id"
            " where f.service_id = %s and f.required and d.archived_at is null"
            " order by f.position",
            (service_id,),
        )
        for key, labels, ftype in await cur.fetchall():
            label = labels.get(language) or labels.get("en") or key
            fields.append(FieldSpec(key, label, ftype))
    cur = await conn.execute(
        "select t.timezone, coalesce((select m.config->>'booking_mode' from tenant_module m"
        " where m.tenant_id = t.id and m.module_key = 'crm'), 'approval')"
        " from tenant t where t.id = app.current_tenant_id()"
    )
    row = await cur.fetchone()
    tz = ZoneInfo(row[0] if row else "Europe/Brussels")
    return Context(services, fields, tz, datetime.now(tz).date(), row[1] if row else "approval")


def _when(iso: str, tz: ZoneInfo) -> str:
    return datetime.fromisoformat(iso).astimezone(tz).strftime("%a %d %b at %H:%M")


def _day(d: date) -> str:
    return d.strftime("%A %d %B")


def prompt(business: str, ctx: Context, booking: dict[str, Any]) -> str:
    """System prompt of the extraction step; its sections are also what the offline
    stand-in reads."""
    lines = [
        f"You help {business} take appointment bookings in a chat. Read the conversation "
        "and extract what the customer's LATEST message says about their booking. Only "
        "fill a field when the customer actually says it; leave the rest null.",
        f"TODAY: {ctx.today.isoformat()} ({ctx.today.strftime('%A')})",
        f"STAGE: {booking.get('stage') or 'service'}",
        f"ASKING: {booking.get('asking') or '-'}",
        "SERVICES:",
        *[f"- {name}" for _, name in ctx.services],
        "OPTIONS:",
        *(
            [
                f"{i + 1}) {_when(o['start'], ctx.tz)}"
                for i, o in enumerate(booking.get("offered") or [])
            ]
            or ["(none)"]
        ),
        "FIELDS:",
        *([f"- {f.key}: {f.label}" for f in ctx.fields] or ["(none)"]),
        "Rules: a day like 'tomorrow' or 'next Friday' becomes a YYYY-MM-DD date after "
        "TODAY. A picked option is its number. 'yes'/'no' only counts as confirm when "
        "STAGE is confirm.",
    ]
    return "\n".join(lines)


def _slot_dict(s: Slot) -> dict[str, str]:
    return {
        "start": s.start.isoformat(),
        "end": s.end.isoformat(),
        "resource_id": str(s.resource_id),
        "resource_name": s.resource_name,
    }


def _parse_date(value: str | None, today: date) -> date | None:
    if not value:
        return None
    try:
        d = date.fromisoformat(value[:10])
    except ValueError:
        return None
    return d if today <= d <= today + timedelta(days=366) else None


def _parse_time(value: str | None) -> time | None:
    if not value:
        return None
    try:
        return time.fromisoformat(value[:5].zfill(5))
    except ValueError:
        return None


async def advance(
    conn: AsyncConnection,
    ctx: Context,
    booking: dict[str, Any],
    update: BookingUpdate,
    *,
    conversation_id: UUID,
) -> Outcome:
    b: dict[str, Any] = {"fields": {}, **booking}
    notes: list[str] = []

    if not ctx.services:
        return Outcome({}, "", handoff="no_services")

    # 1. Service
    if update.service:
        match = next(
            (sid for sid, name in ctx.services if name.lower() == update.service.strip().lower()),
            None,
        ) or next(
            (sid for sid, name in ctx.services if update.service.strip().lower() in name.lower()),
            None,
        )
        if match and str(match) != b.get("service_id"):
            b.update(service_id=str(match), offered=[], chosen=None)
    if not b.get("service_id") and len(ctx.services) == 1:
        b["service_id"] = str(ctx.services[0][0])
    if not b.get("service_id"):
        names = ", ".join(name for _, name in ctx.services)
        b.update(stage="service", asking="service")
        return Outcome(b, f"Which service would you like to book? We offer: {names}.")
    service = await load_service(conn, UUID(b["service_id"]))
    if service is None:
        b.update(service_id=None, stage="service")
        return Outcome(b, "Which service would you like to book?")
    service_name = next((n for sid, n in ctx.services if str(sid) == b["service_id"]), "")

    # 2. Day and time
    wanted_day = _parse_date(update.date, ctx.today)
    wanted_time = _parse_time(update.time)
    if wanted_day and wanted_day.isoformat() != b.get("date"):
        b.update(date=wanted_day.isoformat(), offered=[], chosen=None)
    offered: list[dict[str, str]] = b.get("offered") or []
    if update.option and 1 <= update.option <= len(offered):
        b["chosen"] = offered[update.option - 1]
    elif wanted_time and b.get("date"):
        day = date.fromisoformat(b["date"])
        _, slots = await find_slots(conn, service, day)
        exact = [s for s in slots if s.start.astimezone(ctx.tz).time() == wanted_time]
        if exact:
            b["chosen"] = _slot_dict(exact[0])
        else:
            b["chosen"] = None
            notes.append(f"{wanted_time.strftime('%H:%M')} is not available.")
            after = datetime.combine(day, wanted_time, ctx.tz)
            b["offered"] = [_slot_dict(s) for s in spread(slots, after=after)] if slots else []

    if not b.get("chosen"):
        if not b.get("date"):
            b.update(stage="date", asking="date")
            return Outcome(b, f"Sure, a {service_name}. What day would you like to come?")
        if not b.get("offered"):
            day = date.fromisoformat(b["date"])
            _, slots = await find_slots(conn, service, day)
            if not slots:
                _, later = await find_slots(conn, service, day + timedelta(days=1), SEARCH_DAYS)
                if not later:
                    b.update(stage="date", asking="date", offered=[])
                    return Outcome(
                        b,
                        f"Sorry, there is no free time for a {service_name} on {_day(day)}"
                        f" or the {SEARCH_DAYS} days after. A colleague can help you further.",
                        handoff="no_availability",
                    )
                first = later[0].start.astimezone(ctx.tz).date()
                notes.append(f"{_day(day)} is fully booked.")
                slots = [s for s in later if s.start.astimezone(ctx.tz).date() == first]
                b["date"] = first.isoformat()
            b["offered"] = [_slot_dict(s) for s in spread(slots)]
        options = "; ".join(
            f"{i + 1}) {_when(o['start'], ctx.tz)}" for i, o in enumerate(b["offered"])
        )
        b.update(stage="slot", asking="time")
        intro = " ".join(notes)
        return Outcome(
            b,
            f"{intro} For a {service_name} we have these free times: {options}. "
            "Which one suits you?".strip(),
        )

    # 3. Name and required fields
    if update.name:
        b["name"] = update.name.strip()[:100]
    for answer in update.fields:
        if any(f.key == answer.key for f in ctx.fields) and answer.value.strip():
            b["fields"][answer.key] = answer.value.strip()[:500]
    if not b.get("name"):
        b.update(stage="details", asking="name")
        return Outcome(
            b, f"{_when(b['chosen']['start'], ctx.tz)} is available. May I have your name?"
        )
    missing = [f for f in ctx.fields if not b["fields"].get(f.key)]
    if missing:
        b.update(stage="details", asking=missing[0].key)
        return Outcome(b, f"Thanks, {b['name']}. One more thing: {missing[0].label}?")

    # 4. Confirm, then book
    summary = (
        f"{service_name} on {_when(b['chosen']['start'], ctx.tz)}"
        f" with {b['chosen']['resource_name']}, for {b['name']}"
    )
    if b.get("stage") != "confirm" or update.confirm is None:
        b.update(stage="confirm", asking="confirm")
        return Outcome(b, f"Let me check: {summary}. Shall I book this?")
    if update.confirm == "no":
        b.update(stage="slot", asking="time", chosen=None, offered=[])
        return Outcome(b, "No problem. Which day or time would you prefer instead?")

    status = "confirmed" if ctx.mode == "auto" else "pending_approval"
    try:
        async with conn.transaction():
            cur = await conn.execute(
                "select customer_id from crm_conversation where id = %s", (conversation_id,)
            )
            row = await cur.fetchone()
            customer_id = row[0] if row else None
            await conn.execute(
                "update customer set display_name = coalesce(display_name, %s),"
                " first_name = coalesce(first_name, %s) where id = %s",
                (b["name"], b["name"].split()[0], customer_id),
            )
            cur = await conn.execute(
                "insert into crm_appointment (customer_id, service_id, resource_id, during,"
                " status, source, field_values, conversation_id)"
                " values (%s, %s, %s, tstzrange(%s, %s), %s, 'ai', %s, %s) returning id",
                (
                    customer_id,
                    b["service_id"],
                    b["chosen"]["resource_id"],
                    b["chosen"]["start"],
                    b["chosen"]["end"],
                    status,
                    Jsonb(b["fields"]),
                    conversation_id,
                ),
            )
            created = await cur.fetchone()
    except errors.ExclusionViolation:
        b.update(stage="slot", asking="time", chosen=None, offered=[])
        return Outcome(
            b,
            "Sorry, that time was just taken by someone else. Shall I look for another time?",
        )
    assert created is not None
    done = {"stage": "done", "appointment_id": str(created[0])}
    if status == "confirmed":
        return Outcome(
            done, f"Done! You're booked: {summary}. See you then!", appointment_id=created[0]
        )
    return Outcome(
        done,
        f"Thanks! I've sent your request ({summary}) to the team. "
        "You'll get a message here as soon as it's confirmed.",
        appointment_id=created[0],
    )
