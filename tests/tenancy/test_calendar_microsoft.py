"""Microsoft 365 calendar sync against a fake Microsoft Graph (login + Graph API):
connect with OAuth, Outlook busy time → crm_external_busy, bookings → Outlook events,
change notifications, revoked access, disconnect."""

import json
import uuid
from collections.abc import AsyncIterator, Iterator
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import parse_qs, urlsplit

import httpx2
import psycopg
import pytest
from cryptography.hazmat.primitives.asymmetric import ec
from fastapi.testclient import TestClient
from pydantic import SecretStr

from confluo_api.main import create_app
from confluo_core.auth import TokenVerifier
from confluo_core.db import make_pool
from confluo_core.job_app import build_job_app
from confluo_core.jobs import SETTINGS_KEY, run_jobs_once, worker_context
from confluo_core.modules import discover_modules
from confluo_core.secrets import new_key
from confluo_core.settings import Settings
from confluo_core.webhooks import PROVIDERS_KEY, default_providers
from confluo_crm.calendar import microsoft as ms
from tests.conftest import MakeToken, StaticJWKS
from tests.tenancy.conftest import World, fake_settings

pytestmark = pytest.mark.timeout(60)

GRAPH = "https://graph.test/v1.0"
LOGIN = "https://login.test/common"
SECRETS_KEY = new_key()


def _settings(world: World, **extra: Any) -> Settings:
    return fake_settings(world).model_copy(
        update={
            "secrets_key": SecretStr(SECRETS_KEY),
            "ms_client_id": "client-1",
            "ms_client_secret": SecretStr("shh"),
            "ms_authority": LOGIN,
            "ms_graph_url": GRAPH,
            "dashboard_url": "https://app.test",
            **extra,
        }
    )


def _at(days: int, hour: int) -> datetime:
    base = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
    return base + timedelta(days=days, hours=hour)


def _graph_time(value: datetime) -> dict[str, str]:
    return {"dateTime": value.strftime("%Y-%m-%dT%H:%M:%S.0000000"), "timeZone": "UTC"}


class FakeGraph:
    """Just enough of login.microsoftonline.com and graph.microsoft.com."""

    def __init__(self) -> None:
        self.events: dict[str, dict[str, Any]] = {}
        self.log: list[str] = []  # event ids in change order; a delta token is a position
        self.subscriptions: dict[str, dict[str, Any]] = {}
        self.revoked = False
        self.expires_in = 3600
        self.refreshes = 0
        self.verifiers: list[str] = []
        self.calls: list[str] = []

    def outlook_event(
        self, eid: str, start: datetime, end: datetime, show_as: str = "busy"
    ) -> None:
        self.events[eid] = {
            "id": eid,
            "start": _graph_time(start),
            "end": _graph_time(end),
            "showAs": show_as,
            "isCancelled": False,
            "subject": "Dentist",
        }
        self.log.append(eid)

    def remove(self, eid: str) -> None:
        self.events.pop(eid)
        self.log.append(eid)

    def _delta(self, since: int, start: str | None = None) -> dict[str, Any]:
        if start is None:
            ids = list(dict.fromkeys(reversed(self.log[since:])))
            items = [
                self.events.get(i) or {"id": i, "@removed": {"reason": "deleted"}} for i in ids
            ]
        else:
            items = list(self.events.values())
        return {"value": items, "@odata.deltaLink": f"{GRAPH}/delta-link?pos={len(self.log)}"}

    def tokens(self) -> httpx2.Response:
        return httpx2.Response(
            200,
            json={
                "access_token": f"at-{uuid.uuid4().hex[:6]}",
                "refresh_token": f"rt-{uuid.uuid4().hex[:6]}",
                "expires_in": self.expires_in,
            },
        )

    def handler(self, req: httpx2.Request) -> httpx2.Response:
        url = str(req.url)
        path = req.url.path.removeprefix("/v1.0")
        self.calls.append(f"{req.method} {path}")
        if url.startswith(LOGIN):
            form = parse_qs(req.content.decode())
            if form["grant_type"] == ["authorization_code"]:
                self.verifiers.append(form["code_verifier"][0])
                return self.tokens()
            self.refreshes += 1
            if self.revoked:
                return httpx2.Response(400, json={"error": "invalid_grant"})
            return self.tokens()
        if not req.headers.get("authorization", "").startswith("Bearer at-"):
            return httpx2.Response(401, json={"error": {"code": "InvalidAuthenticationToken"}})
        q = dict(req.url.params)
        match req.method, path:
            case "GET", "/me":
                return httpx2.Response(200, json={"mail": "eva@contoso.be"})
            case "GET", "/me/calendar":
                return httpx2.Response(200, json={"id": "cal-1"})
            case "GET", "/me/calendars/cal-1/calendarView/delta":
                # First page then nextLink, like Graph's paging.
                all_items = self._delta(0, q["startDateTime"])
                half = len(all_items["value"]) // 2
                return httpx2.Response(
                    200,
                    json={
                        "value": all_items["value"][:half],
                        "@odata.nextLink": f"{GRAPH}/next-page?skip={half}",
                    },
                )
            case "GET", "/next-page":
                rest = self._delta(0, "full")
                rest["value"] = rest["value"][int(q["skip"]) :]
                return httpx2.Response(200, json=rest)
            case "GET", "/delta-link":
                return httpx2.Response(200, json=self._delta(int(q["pos"])))
            case "POST", "/me/calendars/cal-1/events":
                body = json.loads(req.content)
                eid = f"ev-{uuid.uuid4().hex[:8]}"
                self.events[eid] = {"id": eid, "isCancelled": False, **body}
                self.log.append(eid)
                return httpx2.Response(201, json=self.events[eid])
            case "POST", "/subscriptions":
                body = json.loads(req.content)
                sid = f"sub-{uuid.uuid4().hex[:8]}"
                self.subscriptions[sid] = body
                return httpx2.Response(201, json={"id": sid, **body})
        if path.startswith("/me/events/"):
            eid = path.rsplit("/", 1)[1]
            if eid not in self.events:
                return httpx2.Response(404, json={"error": {"code": "ErrorItemNotFound"}})
            if req.method == "PATCH":
                self.events[eid].update(json.loads(req.content))
                self.log.append(eid)
                return httpx2.Response(200, json=self.events[eid])
            if req.method == "DELETE":
                self.remove(eid)
                return httpx2.Response(204)
        if path.startswith("/subscriptions/"):
            sid = path.rsplit("/", 1)[1]
            if req.method == "DELETE":
                self.subscriptions.pop(sid, None)
                return httpx2.Response(204)
        return httpx2.Response(404, json={"error": {"code": "NotFound", "path": path}})


@pytest.fixture
def graph() -> Iterator[FakeGraph]:
    fake = FakeGraph()
    ms.set_transport(httpx2.MockTransport(fake.handler))
    yield fake
    ms.set_transport(None)


@pytest.fixture
def settings(world: World) -> Settings:
    return _settings(world, public_api_url="https://api.test")


@pytest.fixture
def api(
    world: World, signing_key: ec.EllipticCurvePrivateKey, settings: Settings
) -> Iterator[TestClient]:
    verifier = TokenVerifier(settings, jwks_client=StaticJWKS(signing_key.public_key()))
    with TestClient(create_app(settings, token_verifier=verifier)) as c:
        yield c


@pytest.fixture
async def worker(world: World, settings: Settings) -> AsyncIterator[Any]:
    pool = make_pool(world.app_url, max_size=4)
    await pool.open()
    app = build_job_app(discover_modules())
    ctx = worker_context(
        pool,
        **{
            PROVIDERS_KEY: default_providers(settings, discover_modules()),
            SETTINGS_KEY: settings,
        },
    )
    async with app.open_async(pool):

        class Worker:
            async def run(self) -> None:
                await run_jobs_once(app, ctx)

        yield Worker()
    await pool.close()


@pytest.fixture
def shop(world: World, chat_shop: dict[str, uuid.UUID], make_token: MakeToken) -> dict[str, Any]:
    """A salon with one stylist (Eva, the owner's resource), a service and a customer."""
    ids = {k: uuid.uuid4() for k in ("resource", "service", "customer", "other")}
    tenant = chat_shop["tenant"]
    with psycopg.connect(world.owner_url, autocommit=True) as conn:
        conn.execute(
            "insert into crm_resource (id, tenant_id, kind, name, member_id)"
            " values (%s, %s, 'staff', 'Eva', %s), (%s, %s, 'staff', 'Tom', null)",
            (ids["resource"], tenant, chat_shop["owner"], ids["other"], tenant),
        )
        conn.execute(
            "insert into crm_service (id, tenant_id, name_i18n, duration_min)"
            ' values (%s, %s, \'{"nl": "Knippen"}\', 30)',
            (ids["service"], tenant),
        )
        conn.execute(
            "insert into customer (id, tenant_id, display_name) values (%s, %s, 'Lotte')",
            (ids["customer"], tenant),
        )
    headers = {
        "Authorization": f"Bearer {make_token(sub=str(chat_shop['owner']))}",
        "X-Tenant-Id": str(tenant),
    }
    return {**ids, "tenant": tenant, "owner": chat_shop["owner"], "headers": headers}


def _connect(api: TestClient, shop: dict[str, Any], graph: FakeGraph) -> dict[str, Any]:
    res = api.get(
        "/api/crm/calendar/microsoft/authorize",
        params={"resource_id": str(shop["resource"])},
        headers=shop["headers"],
    )
    assert res.status_code == 200, res.text
    url = urlsplit(res.json()["url"])
    query = parse_qs(url.query)
    assert url.netloc == "login.test" and query["code_challenge_method"] == ["S256"]
    assert query["redirect_uri"] == ["https://app.test/settings/team/calendar-callback"]
    res = api.post(
        "/api/crm/calendar/microsoft/callback",
        headers=shop["headers"],
        json={"code": "code-1", "state": query["state"][0]},
    )
    assert res.status_code == 200, res.text
    assert graph.verifiers, "PKCE verifier sent with the code"
    out: dict[str, Any] = res.json()
    return out


def _busy(world: World, resource: uuid.UUID) -> list[tuple[str, datetime, datetime]]:
    with psycopg.connect(world.owner_url) as conn:
        rows = conn.execute(
            "select external_event_id, lower(during), upper(during) from crm_external_busy"
            " where resource_id = %s order by lower(during)",
            (resource,),
        ).fetchall()
    return [(r[0], r[1], r[2]) for r in rows]


def _sql(world: World, query: str, *args: Any) -> list[tuple[Any, ...]]:
    with psycopg.connect(world.owner_url, autocommit=True) as conn:
        cur = conn.execute(query, args)
        return cur.fetchall() if cur.description else []


async def test_busy_time_in_outlook_blocks_the_slot(
    api: TestClient, worker: Any, graph: FakeGraph, shop: dict[str, Any], world: World
) -> None:
    graph.outlook_event("dentist", _at(2, 9), _at(2, 10))
    graph.outlook_event("lunch", _at(2, 12), _at(2, 13), show_as="free")
    graph.outlook_event("holiday", _at(3, 0), _at(4, 0), show_as="oof")
    graph.outlook_event("maybe", _at(5, 9), _at(5, 10), show_as="tentative")

    connection = _connect(api, shop, graph)
    assert connection["account_email"] == "eva@contoso.be"
    assert connection["status"] == "active"
    await worker.run()

    busy = _busy(world, shop["resource"])
    assert [b[0] for b in busy] == ["dentist", "holiday", "maybe"]
    assert busy[0][1:] == (_at(2, 9), _at(2, 10))
    status = api.get(f"/api/crm/resources/{shop['resource']}/calendar", headers=shop["headers"])
    assert status.json()["last_synced_at"] is not None
    assert status.json()["live_updates"] is True  # subscription created
    sub = next(iter(graph.subscriptions.values()))
    assert sub["notificationUrl"] == "https://api.test/webhooks/microsoft-calendar"

    # Outlook changes arrive as deltas: one moved, one deleted, one new.
    graph.outlook_event("dentist", _at(2, 14), _at(2, 15))
    graph.remove("holiday")
    graph.outlook_event("gym", _at(6, 7), _at(6, 8))
    res = api.post(f"/api/crm/resources/{shop['resource']}/calendar/sync", headers=shop["headers"])
    assert res.status_code == 202
    await worker.run()
    busy = _busy(world, shop["resource"])
    assert [(b[0], b[1]) for b in busy] == [
        ("dentist", _at(2, 14)),
        ("maybe", _at(5, 9)),
        ("gym", _at(6, 7)),
    ]
    assert "GET /delta-link" in graph.calls


async def test_free_time_skips_outlook_busy(
    api: TestClient, worker: Any, graph: FakeGraph, shop: dict[str, Any], world: World
) -> None:
    day = _at(3, 0)
    graph.outlook_event("dentist", day.replace(hour=9), day.replace(hour=10))
    _connect(api, shop, graph)
    await worker.run()
    res = api.put(
        f"/api/crm/resources/{shop['resource']}/schedule",
        headers=shop["headers"],
        json={"rules": [{"weekday": day.isoweekday(), "start": "00:00", "end": "23:59"}]},
    )
    assert res.status_code == 200, res.text
    free = api.get(
        f"/api/crm/resources/{shop['resource']}/free",
        headers=shop["headers"],
        params={"start": day.date().isoformat(), "days": 1},
    ).json()
    starts = [datetime.fromisoformat(f["start"]) for f in free["spans"]]
    ends = [datetime.fromisoformat(f["end"]) for f in free["spans"]]
    # The free time stops where the dentist starts and resumes when it ends.
    assert day.replace(hour=9) in ends and day.replace(hour=10) in starts


async def test_booking_appears_in_outlook_and_follows_changes(
    api: TestClient, worker: Any, graph: FakeGraph, shop: dict[str, Any], world: World
) -> None:
    _connect(api, shop, graph)
    await worker.run()
    appointment = uuid.uuid4()
    _sql(
        world,
        "insert into crm_appointment (id, tenant_id, customer_id, service_id, resource_id,"
        " during, status, source) values (%s, %s, %s, %s, %s, tstzrange(%s, %s), 'confirmed', 'staff')",
        appointment,
        shop["tenant"],
        shop["customer"],
        shop["service"],
        shop["resource"],
        _at(4, 10),
        _at(4, 10) + timedelta(minutes=30),
    )
    await worker.run()
    [(event_id,)] = _sql(
        world, "select external_event_id from crm_appointment where id = %s", appointment
    )
    event = graph.events[event_id]
    assert event["subject"] == "Knippen – Lotte"
    assert event["start"]["dateTime"] == _at(4, 10).strftime("%Y-%m-%dT%H:%M:%S")
    assert event["transactionId"] == str(appointment)

    # Its own event isn't imported as busy time.
    await worker.run()
    api.post(f"/api/crm/resources/{shop['resource']}/calendar/sync", headers=shop["headers"])
    await worker.run()
    assert _busy(world, shop["resource"]) == []

    # Moved: the event moves.
    _sql(
        world,
        "update crm_appointment set during = tstzrange(%s, %s) where id = %s",
        _at(4, 15),
        _at(4, 15) + timedelta(minutes=30),
        appointment,
    )
    await worker.run()
    assert graph.events[event_id]["start"]["dateTime"].startswith(
        _at(4, 15).strftime("%Y-%m-%dT%H")
    )

    # Deleted in Outlook by hand, then moved again: recreated.
    graph.remove(event_id)
    _sql(
        world,
        "update crm_appointment set during = tstzrange(%s, %s) where id = %s",
        _at(4, 16),
        _at(4, 16) + timedelta(minutes=30),
        appointment,
    )
    await worker.run()
    [(new_id,)] = _sql(
        world, "select external_event_id from crm_appointment where id = %s", appointment
    )
    assert new_id != event_id and new_id in graph.events

    # Reassigned to Tom (no calendar): gone from Eva's Outlook.
    _sql(
        world,
        "update crm_appointment set resource_id = %s where id = %s",
        shop["other"],
        appointment,
    )
    await worker.run()
    assert new_id not in graph.events
    assert _sql(
        world, "select external_event_id from crm_appointment where id = %s", appointment
    ) == [(None,)]

    # Back to Eva, then cancelled: created, then deleted.
    _sql(
        world,
        "update crm_appointment set resource_id = %s where id = %s",
        shop["resource"],
        appointment,
    )
    await worker.run()
    [(back_id,)] = _sql(
        world, "select external_event_id from crm_appointment where id = %s", appointment
    )
    assert back_id in graph.events
    _sql(world, "update crm_appointment set status = 'cancelled' where id = %s", appointment)
    await worker.run()
    assert back_id not in graph.events


async def test_pending_bookings_stay_out_and_deleted_ones_leave(
    api: TestClient, worker: Any, graph: FakeGraph, shop: dict[str, Any], world: World
) -> None:
    _connect(api, shop, graph)
    await worker.run()
    appointment = uuid.uuid4()
    _sql(
        world,
        "insert into crm_appointment (id, tenant_id, customer_id, service_id, resource_id,"
        " during, source) values (%s, %s, %s, %s, %s, tstzrange(%s, %s), 'ai')",
        appointment,
        shop["tenant"],
        shop["customer"],
        shop["service"],
        shop["resource"],
        _at(5, 10),
        _at(5, 11),
    )
    await worker.run()
    assert [e for e in graph.events.values() if "transactionId" in e] == []
    _sql(world, "update crm_appointment set status = 'confirmed' where id = %s", appointment)
    await worker.run()
    [(event_id,)] = _sql(
        world, "select external_event_id from crm_appointment where id = %s", appointment
    )
    assert event_id in graph.events
    _sql(world, "delete from crm_appointment where id = %s", appointment)
    await worker.run()
    assert event_id not in graph.events


async def test_existing_bookings_are_pushed_on_connect(
    api: TestClient, worker: Any, graph: FakeGraph, shop: dict[str, Any], world: World
) -> None:
    _sql(
        world,
        "insert into crm_appointment (tenant_id, customer_id, service_id, resource_id,"
        " during, status, source) values (%s, %s, %s, %s, tstzrange(%s, %s), 'confirmed', 'staff')",
        shop["tenant"],
        shop["customer"],
        shop["service"],
        shop["resource"],
        _at(7, 10),
        _at(7, 11),
    )
    await worker.run()  # no calendar yet: nothing queued, nothing sent
    assert graph.events == {}
    _connect(api, shop, graph)
    await worker.run()
    assert [e["subject"] for e in graph.events.values()] == ["Knippen – Lotte"]


async def test_change_notifications_trigger_a_sync(
    api: TestClient, worker: Any, graph: FakeGraph, shop: dict[str, Any], world: World
) -> None:
    # Graph validates the URL when subscribing: the token comes back as plain text.
    res = api.post("/webhooks/microsoft-calendar?validationToken=abc%20123")
    assert res.status_code == 200 and res.text == "abc 123"

    _connect(api, shop, graph)
    await worker.run()
    [(sub_id, client_state)] = _sql(
        world,
        "select subscription_id, client_state from crm_calendar_connection where resource_id = %s",
        shop["resource"],
    )
    graph.outlook_event("meeting", _at(2, 11), _at(2, 12))

    forged = {
        "value": [{"subscriptionId": sub_id, "clientState": "guess", "changeType": "created"}]
    }
    assert api.post("/webhooks/microsoft-calendar", json=forged).status_code == 200
    await worker.run()
    assert _busy(world, shop["resource"]) == []  # a wrong clientState changes nothing

    real = {
        "value": [{"subscriptionId": sub_id, "clientState": client_state, "changeType": "created"}]
    }
    assert api.post("/webhooks/microsoft-calendar", json=real).json()["status"] == "accepted"
    await worker.run()
    assert [b[0] for b in _busy(world, shop["resource"])] == ["meeting"]


async def test_revoked_access_marks_the_connection(
    api: TestClient, worker: Any, graph: FakeGraph, shop: dict[str, Any], world: World
) -> None:
    graph.expires_in = 0  # every job must refresh the token
    _connect(api, shop, graph)
    await worker.run()
    assert graph.refreshes == 1
    graph.revoked = True
    api.post(f"/api/crm/resources/{shop['resource']}/calendar/sync", headers=shop["headers"])
    await worker.run()
    status = api.get(f"/api/crm/resources/{shop['resource']}/calendar", headers=shop["headers"])
    assert status.json()["status"] == "revoked"
    assert "revoked" in status.json()["last_error"].lower()


async def test_refresh_tokens_are_stored_encrypted(
    api: TestClient, worker: Any, graph: FakeGraph, shop: dict[str, Any], world: World
) -> None:
    _connect(api, shop, graph)
    [(ciphertext,)] = _sql(
        world,
        "select s.ciphertext from secret s join crm_calendar_connection c"
        " on c.credentials_ref = s.id::text where c.resource_id = %s",
        shop["resource"],
    )
    assert b"rt-" not in bytes(ciphertext) and b"access_token" not in bytes(ciphertext)


async def test_disconnect_removes_tokens_busy_time_and_subscription(
    api: TestClient, worker: Any, graph: FakeGraph, shop: dict[str, Any], world: World
) -> None:
    graph.outlook_event("dentist", _at(2, 9), _at(2, 10))
    _connect(api, shop, graph)
    await worker.run()
    [(sub_id,)] = _sql(
        world,
        "select subscription_id from crm_calendar_connection where resource_id = %s",
        shop["resource"],
    )
    assert sub_id in graph.subscriptions and _busy(world, shop["resource"])
    res = api.delete(f"/api/crm/resources/{shop['resource']}/calendar", headers=shop["headers"])
    assert res.status_code == 204
    # (Other tests' connections may share this fake Graph via the periodic sync.)
    assert sub_id not in graph.subscriptions
    assert _busy(world, shop["resource"]) == []
    assert _sql(world, "select count(*) from secret where tenant_id = %s", shop["tenant"]) == [(0,)]
    audited = _sql(
        world,
        "select action from audit_log where tenant_id = %s and entity = 'crm_calendar_connection'"
        " order by id",
        shop["tenant"],
    )
    assert [a[0] for a in audited][:1] == ["insert"] and audited[-1][0] == "delete"
    assert (
        api.get(f"/api/crm/resources/{shop['resource']}/calendar", headers=shop["headers"]).json()
        is None
    )


def test_sign_in_must_be_finished_by_whoever_started_it(
    api: TestClient,
    graph: FakeGraph,
    shop: dict[str, Any],
    world: World,
    make_token: MakeToken,
) -> None:
    res = api.get(
        "/api/crm/calendar/microsoft/authorize",
        params={"resource_id": str(shop["resource"])},
        headers=shop["headers"],
    )
    state = parse_qs(urlsplit(res.json()["url"]).query)["state"][0]
    # Another business (tenant A's owner) can't use it.
    other = {
        "Authorization": f"Bearer {make_token(sub=str(world.user_a))}",
        "X-Tenant-Id": str(world.tenant_a),
    }
    for headers, st in [(other, state), (shop["headers"], state[:-4] + "AAAA")]:
        res = api.post(
            "/api/crm/calendar/microsoft/callback",
            headers=headers,
            json={"code": "c", "state": st},
        )
        assert res.status_code == 400, res.text
    assert graph.verifiers == []


def test_not_configured_is_a_clear_error(
    world: World, signing_key: ec.EllipticCurvePrivateKey, shop: dict[str, Any]
) -> None:
    settings = fake_settings(world)  # no Microsoft app registered
    verifier = TokenVerifier(settings, jwks_client=StaticJWKS(signing_key.public_key()))
    with TestClient(create_app(settings, token_verifier=verifier)) as c:
        res = c.get(
            "/api/crm/calendar/microsoft/authorize",
            params={"resource_id": str(shop["resource"])},
            headers=shop["headers"],
        )
    assert res.status_code == 503
