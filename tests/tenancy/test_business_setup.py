"""Business setup through the API: locations, booking fields, services, resources,
weekly schedules, days off and the resulting free time."""

import uuid
from collections.abc import Iterator
from datetime import date, timedelta
from typing import Any

import psycopg
import pytest
from cryptography.hazmat.primitives.asymmetric import ec
from fastapi.testclient import TestClient

from confluo_api.main import create_app
from confluo_core.auth import TokenVerifier
from confluo_core.settings import Settings
from tests.conftest import MakeToken, StaticJWKS
from tests.tenancy.conftest import World


@pytest.fixture
def shop(world: World) -> dict[str, uuid.UUID]:
    ids = {k: uuid.uuid4() for k in ("tenant", "owner", "staff")}
    with psycopg.connect(world.owner_url, autocommit=True) as conn:
        conn.execute(
            "insert into tenant (id, name, slug) values (%s, 'Setup', %s)",
            (ids["tenant"], f"setup-{ids['tenant'].hex[:10]}"),
        )
        for who in ("owner", "staff"):
            conn.execute(
                "insert into app_user (id, email) values (%s, %s)",
                (ids[who], f"{who}-{ids[who].hex[:6]}@x.be"),
            )
            conn.execute(
                "insert into tenant_member (tenant_id, user_id, role) values (%s, %s, %s)",
                (ids["tenant"], ids[who], who),
            )
    return ids


@pytest.fixture
def api(world: World, signing_key: ec.EllipticCurvePrivateKey) -> Iterator[TestClient]:
    settings = Settings(database_url=world.app_url)
    verifier = TokenVerifier(settings, jwks_client=StaticJWKS(signing_key.public_key()))
    with TestClient(create_app(settings, token_verifier=verifier)) as c:
        yield c


@pytest.fixture
def call(api: TestClient, make_token: MakeToken, shop: dict[str, uuid.UUID]) -> Any:
    def as_(who: str, method: str, url: str, **kw: Any) -> Any:
        h = {
            "Authorization": f"Bearer {make_token(sub=str(shop[who]))}",
            "X-Tenant-Id": str(shop["tenant"]),
        }
        return api.request(method, url, headers=h, **kw)

    return as_


HOURS = {"mon": [{"start": "09:00", "end": "18:00"}], "sat": [{"start": "09:00", "end": "16:00"}]}


def test_locations_with_opening_hours(call: Any) -> None:
    loc = call(
        "owner",
        "post",
        "/api/locations",
        json={"name": "Gent", "timezone": "Europe/Brussels", "opening_hours": HOURS},
    )
    assert loc.status_code == 201
    assert loc.json()["opening_hours"]["mon"] == [{"start": "09:00:00", "end": "18:00:00"}]
    lid = loc.json()["id"]
    res = call("owner", "patch", f"/api/locations/{lid}", json={"address": "Veldstraat 12"})
    assert res.json()["address"] == "Veldstraat 12" and res.json()["name"] == "Gent"
    assert (
        call(
            "owner", "post", "/api/locations", json={"name": "X", "timezone": "Mars/Base"}
        ).status_code
        == 422
    )
    overlap = {"mon": [{"start": "09:00", "end": "12:00"}, {"start": "11:00", "end": "13:00"}]}
    assert (
        call(
            "owner", "post", "/api/locations", json={"name": "X", "opening_hours": overlap}
        ).status_code
        == 422
    )
    backwards = {"tue": [{"start": "18:00", "end": "09:00"}]}
    assert (
        call(
            "owner", "post", "/api/locations", json={"name": "X", "opening_hours": backwards}
        ).status_code
        == 422
    )
    assert (
        call("staff", "patch", f"/api/locations/{lid}", json={"name": "Hacked"}).status_code == 403
    )
    assert call("owner", "delete", f"/api/locations/{lid}").status_code == 204


def test_booking_fields(call: Any) -> None:
    body = {
        "entity": "appointment",
        "key": "hair_length",
        "type": "select",
        "label_i18n": {"en": "Hair length", "nl": "Haarlengte"},
        "options": ["short", "long"],
        "pii_level": "none",
    }
    created = call("owner", "post", "/api/fields", json=body)
    assert created.status_code == 201
    assert call("owner", "post", "/api/fields", json=body).status_code == 409
    assert (
        call("owner", "post", "/api/fields", json=body | {"key": "x", "options": []}).status_code
        == 422
    )
    assert (
        call(
            "owner", "post", "/api/fields", json=body | {"key": "y", "label_i18n": {"xx": "?"}}
        ).status_code
        == 422
    )
    assert call("owner", "post", "/api/fields", json=body | {"key": "Bad Key"}).status_code == 422
    assert call("staff", "post", "/api/fields", json=body | {"key": "z"}).status_code == 403
    assert [
        f["key"]
        for f in call("staff", "get", "/api/fields", params={"entity": "appointment"}).json()
    ] == ["hair_length"]
    fid = created.json()["id"]
    assert call("owner", "delete", f"/api/fields/{fid}").status_code == 204
    assert call("owner", "get", "/api/fields").json() == []
    assert (
        call("owner", "get", "/api/fields", params={"include_archived": True}).json()[0]["archived"]
        is True
    )


def _setup_service(call: Any) -> dict[str, Any]:
    field = call(
        "owner",
        "post",
        "/api/fields",
        json={
            "entity": "appointment",
            "key": "plate",
            "type": "text",
            "label_i18n": {"en": "Licence plate"},
        },
    ).json()
    eva = call("owner", "post", "/api/crm/resources", json={"kind": "staff", "name": "Eva"}).json()
    service = call(
        "owner",
        "post",
        "/api/crm/services",
        json={
            "name_i18n": {"en": "Tyre change", "nl": "Bandenwissel"},
            "duration_min": 45,
            "buffer_after_min": 15,
            "price_cents": 6000,
            "fields": [{"field_id": field["id"], "required": True}],
            "resource_ids": [eva["id"]],
        },
    )
    assert service.status_code == 201, service.text
    return {"field": field, "eva": eva, "service": service.json()}


def test_owner_creates_a_service_with_required_fields(call: Any) -> None:
    made = _setup_service(call)
    service = made["service"]
    assert service["fields"] == [{"field_id": made["field"]["id"], "required": True}]
    assert service["resource_ids"] == [made["eva"]["id"]]
    listed = call("staff", "get", "/api/crm/services").json()
    assert [s["name_i18n"]["nl"] for s in listed] == ["Bandenwissel"]

    updated = call(
        "owner",
        "put",
        f"/api/crm/services/{service['id']}",
        json=service | {"fields": [], "duration_min": 60},
    )
    assert updated.json()["fields"] == [] and updated.json()["duration_min"] == 60
    assert (
        call(
            "staff",
            "post",
            "/api/crm/services",
            json={"name_i18n": {"en": "x"}, "duration_min": 30},
        ).status_code
        == 403
    )
    assert (
        call(
            "owner", "post", "/api/crm/services", json={"name_i18n": {"en": "x"}, "duration_min": 2}
        ).status_code
        == 422
    )


def test_service_rejects_other_tenants_ids(call: Any, world: World) -> None:
    with psycopg.connect(world.owner_url, autocommit=True) as conn:
        foreign = conn.execute(
            "insert into field_definition (tenant_id, entity, key, type) values (%s, 'appointment', %s, 'text') returning id",
            (world.tenant_b, f"f{uuid.uuid4().hex[:6]}"),
        ).fetchone()
    assert foreign is not None
    res = call(
        "owner",
        "post",
        "/api/crm/services",
        json={
            "name_i18n": {"en": "Sneaky"},
            "duration_min": 30,
            "fields": [{"field_id": str(foreign[0])}],
        },
    )
    assert res.status_code == 422


def test_weekly_schedule_days_off_and_free_time(
    call: Any, world: World, shop: dict[str, uuid.UUID]
) -> None:
    made = _setup_service(call)
    rid = made["eva"]["id"]
    rules = [{"weekday": d, "start": "09:00", "end": "17:00"} for d in (1, 2, 3, 4, 5)]
    rules.append({"weekday": 3, "start": "18:00", "end": "20:00"})  # late shift on Wednesday
    assert (
        call(
            "owner", "put", f"/api/crm/resources/{rid}/schedule", json={"rules": rules}
        ).status_code
        == 200
    )
    overlap = rules + [{"weekday": 1, "start": "16:00", "end": "19:00"}]
    assert (
        call(
            "owner", "put", f"/api/crm/resources/{rid}/schedule", json={"rules": overlap}
        ).status_code
        == 422
    )
    assert len(call("staff", "get", f"/api/crm/resources/{rid}/schedule").json()["rules"]) == 6
    assert (
        call("staff", "put", f"/api/crm/resources/{rid}/schedule", json={"rules": []}).status_code
        == 403
    )

    today = date.today()
    monday = today + timedelta(days=(7 - today.weekday()) % 7 or 7)
    tuesday = monday + timedelta(days=1)
    off = call(
        "owner",
        "post",
        f"/api/crm/resources/{rid}/exceptions",
        json={"first_day": str(tuesday), "reason": "Dentist"},
    )
    assert off.status_code == 201
    assert (
        call(
            "owner",
            "post",
            f"/api/crm/resources/{rid}/exceptions",
            json={"first_day": str(monday), "start": "12:00"},
        ).status_code
        == 422
    )

    # An appointment Monday 10:00-10:45 is busy time.
    with psycopg.connect(world.owner_url, autocommit=True) as conn:
        cust = conn.execute(
            "insert into customer (tenant_id, display_name) values (%s, 'Jan') returning id",
            (shop["tenant"],),
        ).fetchone()
        assert cust is not None
        conn.execute(
            "insert into crm_appointment (tenant_id, customer_id, service_id, resource_id, during, status, source)"
            " values (%s, %s, %s, %s, tstzrange((%s || ' 10:00 Europe/Brussels')::timestamptz,"
            " (%s || ' 10:45 Europe/Brussels')::timestamptz), 'confirmed', 'staff')",
            (shop["tenant"], cust[0], made["service"]["id"], rid, str(monday), str(monday)),
        )

    free = call(
        "staff", "get", f"/api/crm/resources/{rid}/free", params={"start": str(monday), "days": 3}
    ).json()
    assert free["timezone"] == "Europe/Brussels"
    got = [(s["start"][:16], s["end"][11:16]) for s in free["spans"]]
    assert got == [
        (f"{monday}T09:00", "10:00"),
        (f"{monday}T10:45", "17:00"),
        # Tuesday is off.
        (f"{monday + timedelta(days=2)}T09:00", "17:00"),
        (f"{monday + timedelta(days=2)}T18:00", "20:00"),
    ]

    # Removing the day off brings Tuesday back.
    ex_id = off.json()["id"]
    assert (
        call("owner", "delete", f"/api/crm/resources/{rid}/exceptions/{ex_id}").status_code == 204
    )
    free = call(
        "staff", "get", f"/api/crm/resources/{rid}/free", params={"start": str(tuesday), "days": 1}
    ).json()
    assert [(s["start"][11:16], s["end"][11:16]) for s in free["spans"]] == [("09:00", "17:00")]

    # A service with appointments can't be deleted, only deactivated.
    assert call("owner", "delete", f"/api/crm/services/{made['service']['id']}").status_code == 409


def test_resource_must_be_a_member_and_local(call: Any, world: World) -> None:
    res = call(
        "owner",
        "post",
        "/api/crm/resources",
        json={"kind": "staff", "name": "Ghost", "member_id": str(world.user_a)},
    )
    assert res.status_code == 422
    with psycopg.connect(world.owner_url) as conn:
        loc = conn.execute(
            "select id from location where tenant_id = %s limit 1", (world.tenant_b,)
        ).fetchone()
    assert loc is not None
    res = call(
        "owner",
        "post",
        "/api/crm/resources",
        json={"kind": "room", "name": "Room", "location_id": str(loc[0])},
    )
    assert res.status_code == 422
