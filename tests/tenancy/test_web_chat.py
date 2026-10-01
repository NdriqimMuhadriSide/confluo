"""Web chat end to end: dashboard settings → widget config → socket → ledger → worker
(adapter + intake graph) → NOTIFY → socket, and the stored unified messages."""

import uuid
from collections.abc import Iterator
from typing import Any

import psycopg
import pytest
from cryptography.hazmat.primitives.asymmetric import ec
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from confluo_api.main import create_app
from confluo_core.auth import TokenVerifier
from tests.conftest import MakeToken, StaticJWKS
from tests.tenancy.conftest import World, fake_settings

pytestmark = pytest.mark.timeout(60)


@pytest.fixture
def api(world: World, signing_key: ec.EllipticCurvePrivateKey) -> Iterator[TestClient]:
    settings = fake_settings(world)
    verifier = TokenVerifier(settings, jwks_client=StaticJWKS(signing_key.public_key()))
    with TestClient(create_app(settings, token_verifier=verifier)) as c:
        yield c


@pytest.fixture
def widget(
    api: TestClient, make_token: MakeToken, chat_shop: dict[str, uuid.UUID]
) -> dict[str, Any]:
    staff = {
        "Authorization": f"Bearer {make_token(sub=str(chat_shop['owner']))}",
        "X-Tenant-Id": str(chat_shop["tenant"]),
    }
    res = api.put(
        "/api/crm/channels/web",
        headers=staff,
        json={
            "enabled": True,
            "settings": {
                "color": "#0f766e",
                "title": {"en": "Salon Zon", "nl": "Salon Zon"},
                "greeting": {"en": "Hi! Ask us anything.", "nl": "Hallo! Stel gerust je vraag."},
                "allowed_origins": ["https://salonzon.be"],
            },
        },
    )
    assert res.status_code == 200, res.text
    return {"key": res.json()["key"], "staff": staff}


def socket_url(key: str, session: str) -> str:
    return f"/public/crm/web/{key}/socket?session={session}"


ORIGIN = {"origin": "https://salonzon.be"}


def test_branding_from_the_dashboard(api: TestClient, widget: dict[str, Any]) -> None:
    assert widget["key"].startswith("wk_")
    config = api.get(f"/public/crm/web/{widget['key']}/config").json()
    assert config["color"] == "#0f766e"
    assert config["greeting"]["nl"] == "Hallo! Stel gerust je vraag."
    assert api.get("/public/crm/web/wk_nope/config").status_code == 404
    bad = api.put(
        "/api/crm/channels/web",
        headers=widget["staff"],
        json={
            "enabled": True,
            "settings": {"color": "red", "allowed_origins": ["ftp://x"]},
        },
    )
    assert bad.status_code == 422
    paused = api.put(
        "/api/crm/channels/web", headers=widget["staff"], json={"enabled": False, "settings": {}}
    )
    assert paused.json()["enabled"] is False
    assert api.get(f"/public/crm/web/{widget['key']}/config").status_code == 404


def test_socket_refuses_unknown_key_bad_session_and_foreign_origin(
    api: TestClient, widget: dict[str, Any]
) -> None:
    session = uuid.uuid4().hex
    for url, headers, code in [
        (socket_url("wk_unknown", session), ORIGIN, 4404),
        (socket_url(widget["key"], "short"), ORIGIN, 4404),
        (socket_url(widget["key"], session), {"origin": "https://evil.example"}, 4403),
    ]:
        with (
            pytest.raises(WebSocketDisconnect) as closed,
            api.websocket_connect(url, headers=headers) as ws,
        ):
            ws.receive_json()
        assert closed.value.code == code


async def test_message_round_trip_and_unified_rows(
    api: TestClient,
    widget: dict[str, Any],
    chat_worker: Any,
    world: World,
    chat_shop: dict[str, uuid.UUID],
) -> None:
    session = uuid.uuid4().hex
    with api.websocket_connect(socket_url(widget["key"], session), headers=ORIGIN) as ws:
        assert ws.receive_json() == {"type": "history", "messages": []}
        ws.send_json({"type": "message", "id": "m1", "text": "Hallo daar!", "lang": "nl-BE"})
        assert ws.receive_json() == {"type": "ack", "id": "m1"}
        # The same client id again (a retry after a network blip) is stored once.
        ws.send_json({"type": "message", "id": "m1", "text": "Hallo daar!", "lang": "nl-BE"})
        assert ws.receive_json() == {"type": "ack", "id": "m1"}

        await chat_worker.run()
        pushed = ws.receive_json()
        assert pushed["type"] == "message"
        assert pushed["message"]["from"] == "ai"
        assert pushed["message"]["text"] == "Hallo! Waarmee kan ik je helpen?"

    with psycopg.connect(world.owner_url) as conn:
        rows = conn.execute(
            "select m.direction, m.sender_type, m.body, m.delivery_status, c.channel, c.language"
            " from crm_message m join crm_conversation c on c.id = m.conversation_id"
            " join customer_identity i on i.customer_id = c.customer_id"
            " where i.type = 'web_session' and i.value_normalized = %s order by m.created_at",
            (session,),
        ).fetchall()
    assert [r[:4] for r in rows] == [
        ("inbound", "customer", "Hallo daar!", None),
        ("outbound", "ai", "Hallo! Waarmee kan ik je helpen?", "sent"),
    ]
    assert {r[4] for r in rows} == {"web"} and rows[0][5] == "nl"

    # Reconnecting (page reload) brings the conversation back.
    with api.websocket_connect(socket_url(widget["key"], session), headers=ORIGIN) as ws:
        history = ws.receive_json()["messages"]
    assert [(m["from"], m["text"]) for m in history] == [
        ("customer", "Hallo daar!"),
        ("ai", "Hallo! Waarmee kan ik je helpen?"),
    ]


def test_invalid_messages_are_rejected(api: TestClient, widget: dict[str, Any]) -> None:
    with api.websocket_connect(socket_url(widget["key"], uuid.uuid4().hex), headers=ORIGIN) as ws:
        ws.receive_json()
        ws.send_json({"type": "message", "id": "bad id!", "text": "x"})
        assert ws.receive_json()["error"] == "invalid"
        ws.send_json({"type": "message", "id": "m2", "text": "x" * 2001})
        assert ws.receive_json()["error"] == "invalid"
