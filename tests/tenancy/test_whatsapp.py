"""WhatsApp Cloud API channel against a fake Graph API: webhook verification, signed
inbound messages → AI reply on WhatsApp, delivery receipts, settings with an
encrypted token, and booking confirmations sent on WhatsApp."""

import hashlib
import hmac
import json
import uuid
from collections.abc import Iterator
from typing import Any

import httpx2
import psycopg
import pytest
from cryptography.hazmat.primitives.asymmetric import ec
from fastapi.testclient import TestClient
from pydantic import SecretStr

from confluo_api.main import create_app
from confluo_core.auth import TokenVerifier
from confluo_core.secrets import new_key
from confluo_core.settings import Settings
from confluo_crm.channels import whatsapp
from tests.conftest import MakeToken, StaticJWKS
from tests.tenancy.conftest import World, fake_settings

pytestmark = pytest.mark.timeout(60)

APP_SECRET = "meta-app-secret"
VERIFY = "verify-me"
TOKEN = "EAAG-test-access-token-123456"


class FakeGraph:
    def __init__(self) -> None:
        self.sent: list[dict[str, Any]] = []
        self.auth: list[str] = []

    def handler(self, req: httpx2.Request) -> httpx2.Response:
        self.auth.append(req.headers.get("authorization", ""))
        body = json.loads(req.content)
        self.sent.append({"path": req.url.path, **body})
        return httpx2.Response(200, json={"messages": [{"id": f"wamid.OUT{len(self.sent)}"}]})


@pytest.fixture
def settings(world: World) -> Settings:
    return fake_settings(world).model_copy(
        update={
            "secrets_key": SecretStr(new_key()),
            "whatsapp_app_secret": SecretStr(APP_SECRET),
            "whatsapp_verify_token": VERIFY,
            "whatsapp_graph_url": "https://graph.test/v23.0",
        }
    )


@pytest.fixture
def graph(settings: Settings) -> Iterator[FakeGraph]:
    fake = FakeGraph()
    whatsapp.configure(settings, httpx2.MockTransport(fake.handler))
    yield fake
    whatsapp.configure(None, None)


@pytest.fixture
def api(
    world: World, signing_key: ec.EllipticCurvePrivateKey, settings: Settings
) -> Iterator[TestClient]:
    verifier = TokenVerifier(settings, jwks_client=StaticJWKS(signing_key.public_key()))
    with TestClient(create_app(settings, token_verifier=verifier)) as c:
        yield c


@pytest.fixture
def shop(salon: dict[str, Any], make_token: MakeToken) -> dict[str, Any]:
    number = str(uuid.uuid4().int)[:15]  # phone_number_ids are unique across businesses
    headers = {
        "Authorization": f"Bearer {make_token(sub=str(salon['owner']))}",
        "X-Tenant-Id": str(salon["tenant"]),
    }
    return {**salon, "number": number, "headers": headers}


def _connect(api: TestClient, shop: dict[str, Any]) -> dict[str, Any]:
    res = api.put(
        "/api/crm/channels/whatsapp",
        headers=shop["headers"],
        json={
            "enabled": True,
            "phone_number_id": shop["number"],
            "display_phone": "+1 555 0100",
            "access_token": TOKEN,
        },
    )
    assert res.status_code == 200, res.text
    out: dict[str, Any] = res.json()
    return out


def _payload(
    number: str, *, text: str, wa_id: str = "32470112233", mid: str | None = None
) -> dict[str, Any]:
    return {
        "object": "whatsapp_business_account",
        "entry": [
            {
                "id": "WABA",
                "changes": [
                    {
                        "field": "messages",
                        "value": {
                            "messaging_product": "whatsapp",
                            "metadata": {
                                "display_phone_number": "15550100",
                                "phone_number_id": number,
                            },
                            "contacts": [{"profile": {"name": "Sara Janssens"}, "wa_id": wa_id}],
                            "messages": [
                                {
                                    "from": wa_id,
                                    "id": mid or f"wamid.IN{uuid.uuid4().hex[:10]}",
                                    "timestamp": "1790000000",
                                    "type": "text",
                                    "text": {"body": text},
                                }
                            ],
                        },
                    }
                ],
            }
        ],
    }


def _status(number: str, wamid: str, status: str) -> dict[str, Any]:
    return {
        "object": "whatsapp_business_account",
        "entry": [
            {
                "id": "WABA",
                "changes": [
                    {
                        "field": "messages",
                        "value": {
                            "messaging_product": "whatsapp",
                            "metadata": {"phone_number_id": number},
                            "statuses": [{"id": wamid, "status": status, "recipient_id": "x"}],
                        },
                    }
                ],
            }
        ],
    }


def _post(api: TestClient, payload: dict[str, Any], secret: str = APP_SECRET) -> Any:
    body = json.dumps(payload).encode()
    sig = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return api.post(
        "/webhooks/whatsapp",
        content=body,
        headers={"Content-Type": "application/json", "X-Hub-Signature-256": sig},
    )


def test_meta_verifies_the_webhook(api: TestClient, graph: FakeGraph) -> None:
    ok = api.get(
        "/webhooks/whatsapp",
        params={"hub.mode": "subscribe", "hub.verify_token": VERIFY, "hub.challenge": "1158201444"},
    )
    assert ok.status_code == 200 and ok.text == "1158201444"
    bad = api.get(
        "/webhooks/whatsapp",
        params={"hub.mode": "subscribe", "hub.verify_token": "nope", "hub.challenge": "1"},
    )
    assert bad.status_code == 403


def test_settings_store_the_token_encrypted(
    api: TestClient, graph: FakeGraph, shop: dict[str, Any], world: World
) -> None:
    first = api.put(
        "/api/crm/channels/whatsapp",
        headers=shop["headers"],
        json={"enabled": True, "phone_number_id": shop["number"]},
    )
    assert first.status_code == 422  # a token is needed the first time
    out = _connect(api, shop)
    assert out == {
        "enabled": True,
        "phone_number_id": shop["number"],
        "display_phone": "+1 555 0100",
        "has_token": True,
        "webhook_ready": True,
    }
    # Later changes may leave the token out: it's kept.
    res = api.put(
        "/api/crm/channels/whatsapp",
        headers=shop["headers"],
        json={"enabled": False, "phone_number_id": shop["number"]},
    )
    assert res.json()["enabled"] is False and res.json()["has_token"] is True
    with psycopg.connect(world.owner_url) as conn:
        rows = conn.execute(
            "select s.ciphertext from secret s where s.tenant_id = %s", (shop["tenant"],)
        ).fetchall()
        audited = conn.execute(
            "select action from audit_log where tenant_id = %s and entity = 'crm_channel_connection'"
            " order by id",
            (shop["tenant"],),
        ).fetchall()
    assert len(rows) == 1 and TOKEN.encode() not in bytes(rows[0][0])
    assert [a[0] for a in audited] == ["insert", "update"]


async def test_inbound_message_gets_an_ai_reply_on_whatsapp(
    api: TestClient, graph: FakeGraph, chat_worker: Any, shop: dict[str, Any], world: World
) -> None:
    _connect(api, shop)
    # Unsigned or wrongly signed deliveries are refused.
    assert _post(api, _payload(shop["number"], text="Hello"), secret="wrong").status_code == 401

    payload = _payload(shop["number"], text="Hello!", mid="wamid.IN1")
    assert _post(api, payload).json()["status"] == "accepted"
    assert _post(api, payload).json()["status"] == "duplicate"  # Meta retries
    await chat_worker.run()

    [sent] = graph.sent
    assert sent["path"] == f"/v23.0/{shop['number']}/messages"
    assert sent["to"] == "32470112233" and sent["type"] == "text"
    assert sent["text"]["body"] == "Hello! How can I help you?"
    assert graph.auth == [f"Bearer {TOKEN}"]

    with psycopg.connect(world.owner_url) as conn:
        rows = conn.execute(
            "select m.direction, m.body, m.external_id, m.delivery_status, c.channel, cu.display_name"
            " from crm_message m join crm_conversation c on c.id = m.conversation_id"
            " join customer cu on cu.id = c.customer_id where m.tenant_id = %s order by m.created_at",
            (shop["tenant"],),
        ).fetchall()
    assert rows == [
        ("inbound", "Hello!", "wamid.IN1", None, "whatsapp", "Sara Janssens"),
        (
            "outbound",
            "Hello! How can I help you?",
            "wamid.OUT1",
            "sent",
            "whatsapp",
            "Sara Janssens",
        ),
    ]

    # Receipts: delivered, then read; a late "delivered" doesn't undo "read".
    for status in ("delivered", "read", "delivered"):
        assert _post(api, _status(shop["number"], "wamid.OUT1", status)).status_code == 200
    await chat_worker.run()
    with psycopg.connect(world.owner_url) as conn:
        row = conn.execute(
            "select delivery_status from crm_message where external_id = 'wamid.OUT1'"
        ).fetchone()
    assert row == ("read",)


async def test_booking_by_whatsapp_and_confirmation_after_approval(
    api: TestClient, graph: FakeGraph, chat_worker: Any, shop: dict[str, Any]
) -> None:
    _connect(api, shop)
    for text in ["I want to book an appointment", "tomorrow", "1", "my name is Sara", "yes"]:
        _post(api, _payload(shop["number"], text=text))
        await chat_worker.run()
    assert "sent your request" in graph.sent[-1]["text"]["body"]

    from datetime import datetime, timedelta
    from zoneinfo import ZoneInfo

    today = datetime.now(ZoneInfo("Europe/Brussels")).date()
    [pending] = api.get(
        "/api/crm/appointments",
        headers=shop["headers"],
        params={"start": today.isoformat(), "days": 3},
    ).json()["appointments"]
    assert pending["status"] == "pending_approval" and pending["customer_name"] == "Sara Janssens"
    api.post(f"/api/crm/appointments/{pending['id']}/approve", headers=shop["headers"])
    assert graph.sent[-1]["text"]["body"].startswith("Good news")
    assert (today + timedelta(days=1)).strftime("%d/%m") in graph.sent[-1]["text"]["body"]


def test_unknown_number_is_not_routed(api: TestClient, graph: FakeGraph) -> None:
    # Accepted (Meta must get a 200), but no business owns this number.
    assert _post(api, _payload("999999999", text="hi")).json()["status"] == "accepted"
