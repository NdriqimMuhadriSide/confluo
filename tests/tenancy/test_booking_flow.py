"""Booking through the intake graph (offline brain): service → day → real free times →
name and required fields → summary → booked only after "yes"."""

import re
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import psycopg

from confluo_crm.availability import Span
from confluo_crm.slots import Service, cut
from tests.tenancy.conftest import World
from tests.tenancy.test_intake_graph import RecordingAdapter, _connection, say

TZ = ZoneInfo("Europe/Brussels")


def _mode(world: World, tenant: uuid.UUID, mode: str) -> None:
    with psycopg.connect(world.owner_url, autocommit=True) as conn:
        conn.execute(
            "insert into tenant_module (tenant_id, module_key, enabled, config)"
            " values (%s, 'crm', true, jsonb_build_object('booking_mode', %s::text))"
            " on conflict (tenant_id, module_key) do update set config = excluded.config",
            (tenant, mode),
        )


def _appointments(world: World, tenant: uuid.UUID) -> list[tuple[Any, ...]]:
    with psycopg.connect(world.owner_url) as conn:
        return conn.execute(
            "select a.status, a.source, lower(a.during), upper(a.during), c.display_name,"
            " a.field_values, a.conversation_id is not null from crm_appointment a"
            " join customer c on c.id = a.customer_id where a.tenant_id = %s",
            (tenant,),
        ).fetchall()


async def test_book_step_by_step_in_approval_mode(
    chat_worker: Any, world: World, salon: dict[str, Any]
) -> None:
    connection = await _connection(world, salon["tenant"])
    session, adapter = uuid.uuid4().hex, RecordingAdapter()

    async def chat(text: str) -> str:
        await say(chat_worker, world, salon, connection, session, text, adapter)
        return adapter.sent[-1].text

    assert "What day" in await chat("I want to book an appointment")
    offer = await chat("tomorrow")
    assert "1)" in offer and "Which one suits you?" in offer
    tomorrow = (datetime.now(TZ) + timedelta(days=1)).date()
    assert tomorrow.strftime("%d %b") in offer
    assert "May I have your name?" in await chat("2")
    summary = await chat("my name is Lotte Peeters")
    assert "Shall I book this?" in summary and "Lotte Peeters" in summary
    assert _appointments(world, salon["tenant"]) == []  # nothing booked before "yes"

    done = await chat("yes")
    assert "sent your request" in done
    [(status, source, start, end, name, fields, linked)] = _appointments(world, salon["tenant"])
    assert (status, source, name, linked) == ("pending_approval", "ai", "Lotte Peeters", True)
    assert end - start == timedelta(minutes=30)
    assert start.astimezone(TZ).date() == tomorrow
    assert start.astimezone(TZ).strftime("%H:%M") in offer.split("2)")[1]

    # The conversation stays with the AI; a new request starts a new booking.
    assert "What day" in await chat("I'd like to book another appointment")


async def test_auto_mode_exact_time_and_required_field(
    chat_worker: Any, world: World, salon: dict[str, Any]
) -> None:
    _mode(world, salon["tenant"], "auto")
    with psycopg.connect(world.owner_url, autocommit=True) as conn:
        row = conn.execute(
            "insert into field_definition (tenant_id, entity, key, type, label_i18n)"
            " values (%s, 'appointment', 'hair_length', 'text', '{\"en\": \"How long is your hair\"}')"
            " returning id",
            (salon["tenant"],),
        ).fetchone()
        assert row is not None
        conn.execute(
            "insert into crm_service_field (tenant_id, service_id, field_definition_id)"
            " values (%s, %s, %s)",
            (salon["tenant"], salon["service"], row[0]),
        )
    connection = await _connection(world, salon["tenant"])
    session, adapter = uuid.uuid4().hex, RecordingAdapter()

    async def chat(text: str) -> str:
        await say(chat_worker, world, salon, connection, session, text, adapter)
        return adapter.sent[-1].text

    day = (datetime.now(TZ) + timedelta(days=2)).date()
    asked = await chat(f"Can I book a haircut on {day.isoformat()} at 14:00?")
    assert "May I have your name?" in asked and "14:00" in asked
    assert "How long is your hair" in await chat("Ik ben Jan")
    assert "Shall I book this?" in await chat("shoulder length")
    assert "Done! You're booked" in await chat("ja")
    [(status, _, start, _, name, fields, _)] = _appointments(world, salon["tenant"])
    assert status == "confirmed" and name == "Jan"
    assert fields == {"hair_length": "shoulder length"}
    assert start == datetime.combine(day, datetime.min.time(), TZ).replace(hour=14)

    # The same time again: taken, so other times are offered instead.
    session2 = uuid.uuid4().hex
    out = await say(
        chat_worker,
        world,
        salon,
        connection,
        session2,
        f"Can I book a haircut on {day.isoformat()} at 14:00?",
        adapter,
    )
    assert "14:00 is not available" in adapter.sent[-1].text
    assert "1)" in adapter.sent[-1].text
    assert out["state"]["booking"]["stage"] == "slot"


async def test_full_day_offers_the_next_free_day(
    chat_worker: Any, world: World, salon: dict[str, Any]
) -> None:
    day = (datetime.now(TZ) + timedelta(days=3)).date()
    with psycopg.connect(world.owner_url, autocommit=True) as conn:
        conn.execute(
            "insert into crm_availability_exception (tenant_id, resource_id, kind, during)"
            " values (%s, %s, 'off', tstzrange(%s, %s))",
            (
                salon["tenant"],
                salon["eva"],
                datetime.combine(day, datetime.min.time(), TZ),
                datetime.combine(day + timedelta(days=1), datetime.min.time(), TZ),
            ),
        )
    connection = await _connection(world, salon["tenant"])
    adapter = RecordingAdapter()
    await say(
        chat_worker,
        world,
        salon,
        connection,
        uuid.uuid4().hex,
        f"I want to book a haircut on {day.isoformat()}",
        adapter,
    )
    text = adapter.sent[-1].text
    assert "fully booked" in text
    assert (day + timedelta(days=1)).strftime("%d %b") in text


def test_slots_respect_duration_buffers_and_lead_time() -> None:
    service = Service(
        uuid.uuid4(),
        {"en": "Colour"},
        timedelta(minutes=60),
        timedelta(minutes=15),
        timedelta(minutes=15),
    )
    t = datetime(2030, 1, 7, 9, 0, tzinfo=UTC)
    free = [Span(t, t + timedelta(hours=2))]
    starts = [s.strftime("%H:%M") for s, _ in cut(service, free, not_before=t)]
    # 15 min before and after must fit too: 09:15 … 09:45.
    assert starts == ["09:15", "09:30", "09:45"]
    later = [
        s.strftime("%H:%M") for s, _ in cut(service, free, not_before=t + timedelta(minutes=31))
    ]
    assert later == ["09:45"]


async def test_part_of_day_only_offers_those_times(
    chat_worker: Any, world: World, salon: dict[str, Any]
) -> None:
    connection = await _connection(world, salon["tenant"])
    session, adapter = uuid.uuid4().hex, RecordingAdapter()
    day = (datetime.now(TZ) + timedelta(days=2)).date()
    await say(
        chat_worker,
        world,
        salon,
        connection,
        session,
        f"I want to book a haircut on {day.isoformat()} in the afternoon",
        adapter,
    )
    offer = adapter.sent[-1].text
    hours = [int(h) for h in re.findall(r"at (\d{2}):\d{2}", offer)]
    assert hours and all(12 <= h < 17 for h in hours), offer
    assert "not available" not in offer
