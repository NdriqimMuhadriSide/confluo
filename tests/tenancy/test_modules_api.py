"""Per-tenant module enablement: routes, nav and validated config."""

import uuid
from collections.abc import Iterator
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
    """A fresh tenant with an owner and a staff member."""
    ids = {k: uuid.uuid4() for k in ("tenant", "owner", "staff")}
    with psycopg.connect(world.owner_url, autocommit=True) as conn:
        conn.execute(
            "insert into tenant (id, name, slug) values (%s, 'Shop', %s)",
            (ids["tenant"], f"shop-{ids['tenant'].hex[:10]}"),
        )
        for user, role in (("owner", "owner"), ("staff", "staff")):
            conn.execute(
                "insert into app_user (id, email) values (%s, %s)", (ids[user], f"{user}@x.be")
            )
            conn.execute(
                "insert into tenant_member (tenant_id, user_id, role) values (%s, %s, %s)",
                (ids["tenant"], ids[user], role),
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
        headers = {
            "Authorization": f"Bearer {make_token(sub=str(shop[who]))}",
            "X-Tenant-Id": str(shop["tenant"]),
        }
        return api.request(method, url, headers=headers, **kw)

    return as_


def test_crm_is_registered_and_on_by_default(call: Any) -> None:
    [crm] = call("owner", "get", "/api/modules").json()
    assert crm["key"] == "crm" and crm["enabled"] is True
    assert crm["config"] == {
        "booking_mode": "approval",
        "reminder_hours_before": 24,
        "ai_enabled": True,
    }
    assert crm["config_schema"]["properties"]["booking_mode"]["enum"] == ["approval", "auto"]
    assert call("owner", "get", "/api/crm/status").json() == {"module": "crm", "status": "ok"}


def test_disabling_hides_routes_and_nav(call: Any) -> None:
    manifest = call("owner", "get", "/api/me/manifest").json()
    assert manifest["modules"] == ["crm"]
    assert {n["key"] for n in manifest["nav"]} >= {"inbox", "calendar", "knowledge"}

    res = call("owner", "put", "/api/modules/crm", json={"enabled": False})
    assert res.status_code == 200 and res.json()["enabled"] is False
    assert call("owner", "get", "/api/crm/status").status_code == 404
    assert call("staff", "get", "/api/crm/status").status_code == 404
    off = call("owner", "get", "/api/me/manifest").json()
    assert (off["modules"], off["nav"]) == ([], [])

    call("owner", "put", "/api/modules/crm", json={"enabled": True})
    assert call("owner", "get", "/api/crm/status").status_code == 200


def test_disabling_is_per_tenant(
    call: Any, api: TestClient, make_token: MakeToken, world: World
) -> None:
    call("owner", "put", "/api/modules/crm", json={"enabled": False})
    other = {
        "Authorization": f"Bearer {make_token(sub=str(world.user_a))}",
        "X-Tenant-Id": str(world.tenant_a),
    }
    assert api.get("/api/crm/status", headers=other).status_code == 200


def test_config_is_validated_against_the_schema(call: Any) -> None:
    bad = call("owner", "put", "/api/modules/crm", json={"config": {"booking_mode": "yolo"}})
    assert bad.status_code == 422
    assert bad.json()["detail"][0]["loc"] == ["config", "booking_mode"]
    too_big = call(
        "owner", "put", "/api/modules/crm", json={"config": {"reminder_hours_before": 999}}
    )
    assert too_big.status_code == 422

    ok = call("owner", "put", "/api/modules/crm", json={"config": {"booking_mode": "auto"}})
    assert ok.status_code == 200
    assert ok.json()["config"]["booking_mode"] == "auto"
    # Fields left out fall back to defaults; the stored config is the validated one.
    assert ok.json()["config"]["reminder_hours_before"] == 24
    [crm] = call("owner", "get", "/api/modules").json()
    assert crm["config"]["booking_mode"] == "auto"


def test_staff_cannot_change_modules(call: Any) -> None:
    assert call("staff", "get", "/api/modules").status_code == 403
    assert call("staff", "put", "/api/modules/crm", json={"enabled": False}).status_code == 403


def test_nav_respects_permissions(call: Any) -> None:
    staff_nav = {n["key"] for n in call("staff", "get", "/api/me/manifest").json()["nav"]}
    assert "knowledge" not in staff_nav  # needs crm.kb.edit
    assert "inbox" in staff_nav


def test_unknown_module_is_404(call: Any) -> None:
    assert call("owner", "put", "/api/modules/nope", json={"enabled": True}).status_code == 404
