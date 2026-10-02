"""Calendar and inbox endpoints: list, staff booking, approve/reject/cancel (the
customer is told in their chat), free slots, staff replies and hand-back."""

import uuid
from collections.abc import Iterator
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import psycopg
import pytest
from cryptography.hazmat.primitives.asymmetric import ec
from fastapi.testclient import TestClient

from confluo_api.main import create_app
from confluo_core.auth import TokenVerifier
from tests.conftest import MakeToken, StaticJWKS
from tests.tenancy.conftest import World, fake_settings
from tests.tenancy.test_intake_graph import RecordingAdapter, _connection, say

TZ = ZoneInfo("Europe/Brussels")


@pytest.fixture
def api(world: World, signing_key: ec.EllipticCurvePrivateKey) -> Iterator[TestClient]:
    settings = fake_settings(world)
    verifier = TokenVerifier(settings, jwks_client=StaticJWKS(signing_key.public_key()))
    with TestClient(create_app(settings, token_verifier=verifier)) as c:
        yield c


@pytest.fixture
def h(salon: dict[str, Any], make_token: MakeToken) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {make_token(sub=str(salon['owner']))}",
        "X-Tenant-Id": str(salon["tenant"]),
    }


def _audited(world: World, tenant: uuid.UUID, entity: str) -> list[str]:
    with psycopg.connect(world.owner_url) as conn:
        rows = conn.execute(
            "select action from audit_log where tenant_id = %s and entity = %s order by id",
            (tenant, entity),
        ).fetchall()
    return [r[0] for r in rows]


def _tomorrow(hour: int) -> datetime:
    day = (datetime.now(TZ) + timedelta(days=1)).date()
    return datetime.combine(day, datetime.min.time(), TZ).replace(hour=hour)


def test_staff_books_lists_and_cancels(
    api: TestClient, salon: dict[str, Any], h: dict[str, str], world: World
) -> None:
    start = _tomorrow(10)
    body = {
        "service_id": str(salon["service"]),
        "resource_id": str(salon["eva"]),
        "start": start.isoformat(),
        "customer_name": "Walk-in Ann",
    }
    res = api.post("/api/crm/appointments", headers=h, json=body)
    assert res.status_code == 201, res.text
    a = res.json()
    assert (a["status"], a["source"], a["customer_name"]) == ("confirmed", "staff", "Walk-in Ann")
    assert datetime.fromisoformat(a["end"]) - datetime.fromisoformat(a["start"]) == timedelta(
        minutes=30
    )
    # The same time again: refused, never double-booked.
    assert api.post("/api/crm/appointments", headers=h, json=body).status_code == 409

    listed = api.get(
        "/api/crm/appointments", headers=h, params={"start": start.date().isoformat(), "days": 1}
    ).json()["appointments"]
    assert [x["id"] for x in listed] == [a["id"]]
    assert listed[0]["resource_name"] == "Eva" and listed[0]["service_name"]["nl"] == "Knippen"

    # The slot is gone from the free slots.
    slots = api.get(
        f"/api/crm/services/{salon['service']}/slots",
        headers=h,
        params={"start": start.date().isoformat()},
    ).json()
    starts = {datetime.fromisoformat(s["start"]) for s in slots}
    assert start not in starts and start + timedelta(minutes=30) in starts

    res = api.post(f"/api/crm/appointments/{a['id']}/cancel", headers=h)
    assert res.json()["status"] == "cancelled"
    assert api.post(f"/api/crm/appointments/{a['id']}/cancel", headers=h).status_code == 409
    assert _audited(world, salon["tenant"], "crm_appointment") == ["insert", "update"]


async def test_approving_a_chat_booking_tells_the_customer(
    api: TestClient,
    chat_worker: Any,
    salon: dict[str, Any],
    h: dict[str, str],
    world: World,
) -> None:
    connection = await _connection(world, salon["tenant"])
    session, adapter = uuid.uuid4().hex, RecordingAdapter()
    for text in ["Ik wil een afspraak maken", "morgen", "1", "Ik ben Lotte", "ja"]:
        await say(chat_worker, world, salon, connection, session, text, adapter)

    [pending] = api.get(
        "/api/crm/appointments",
        headers=h,
        params={"start": datetime.now(TZ).date().isoformat(), "days": 3},
    ).json()["appointments"]
    assert pending["status"] == "pending_approval" and pending["customer_name"] == "Lotte"
    res = api.post(f"/api/crm/appointments/{pending['id']}/approve", headers=h)
    assert res.json()["status"] == "confirmed"

    conv = api.get(f"/api/crm/conversations/{pending['conversation_id']}", headers=h).json()
    last = conv["messages"][-1]
    assert last["sender_type"] == "system" and last["body"].startswith("Goed nieuws")
    assert conv["appointments"] == 1
    # Approving twice isn't possible; rejecting a confirmed booking neither.
    assert api.post(f"/api/crm/appointments/{pending['id']}/approve", headers=h).status_code == 409
    assert api.post(f"/api/crm/appointments/{pending['id']}/reject", headers=h).status_code == 409
    assert _audited(world, salon["tenant"], "crm_appointment")[-1] == "update"


async def test_rejecting_tells_the_customer_too(
    api: TestClient, chat_worker: Any, salon: dict[str, Any], h: dict[str, str], world: World
) -> None:
    connection = await _connection(world, salon["tenant"])
    session, adapter = uuid.uuid4().hex, RecordingAdapter()
    for text in ["I want to book an appointment", "tomorrow", "3", "my name is Bo", "yes"]:
        await say(chat_worker, world, salon, connection, session, text, adapter)
    [pending] = api.get(
        "/api/crm/appointments",
        headers=h,
        params={"start": datetime.now(TZ).date().isoformat(), "days": 3},
    ).json()["appointments"]
    res = api.post(f"/api/crm/appointments/{pending['id']}/reject", headers=h)
    assert res.json()["status"] == "cancelled"
    conv = api.get(f"/api/crm/conversations/{pending['conversation_id']}", headers=h).json()
    assert conv["messages"][-1]["body"].startswith("Sorry, we can't take your appointment")


async def test_inbox_lists_reads_replies_and_hands_back(
    api: TestClient, chat_worker: Any, salon: dict[str, Any], h: dict[str, str], world: World
) -> None:
    connection = await _connection(world, salon["tenant"])
    adapter = RecordingAdapter()
    out = await say(chat_worker, world, salon, connection, uuid.uuid4().hex, "Hello there", adapter)
    conversation = str(out["conversation"])

    [listed] = api.get("/api/crm/conversations", headers=h).json()
    assert listed["id"] == conversation and listed["handler"] == "ai"
    assert listed["last_message"] == "Hello! How can I help you?"

    res = api.post(
        f"/api/crm/conversations/{conversation}/messages",
        headers=h,
        json={"text": "Hi, this is Eva from the salon."},
    )
    assert res.status_code == 200, res.text
    detail = res.json()
    assert detail["handler"] == "human"
    assert [(m["sender_type"], m["body"]) for m in detail["messages"]][-1] == (
        "staff",
        "Hi, this is Eva from the salon.",
    )
    assert detail["messages"][-1]["delivery_status"] == "sent"

    res = api.post(f"/api/crm/conversations/{conversation}/handback", headers=h)
    assert res.json()["handler"] == "ai"
    assert _audited(world, salon["tenant"], "crm_conversation")[-2:] == ["update", "update"]
    assert "insert" in _audited(world, salon["tenant"], "crm_message")
    assert api.get(f"/api/crm/conversations/{uuid.uuid4()}", headers=h).status_code == 404
