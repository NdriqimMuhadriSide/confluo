"""The same isolation, through HTTP: X-Tenant-Id header, membership check, 403."""

import uuid
from collections.abc import Iterator

import pytest
from cryptography.hazmat.primitives.asymmetric import ec
from fastapi.testclient import TestClient

from confluo_api.main import create_app
from confluo_core.auth import TokenVerifier
from confluo_core.settings import Settings
from tests.conftest import MakeToken, StaticJWKS
from tests.tenancy.conftest import World


@pytest.fixture
def api(world: World, signing_key: ec.EllipticCurvePrivateKey) -> Iterator[TestClient]:
    settings = Settings(database_url=world.app_url)
    verifier = TokenVerifier(settings, jwks_client=StaticJWKS(signing_key.public_key()))
    with TestClient(create_app(settings, token_verifier=verifier)) as client:
        yield client


def headers(token: str, tenant: uuid.UUID | None = None) -> dict[str, str]:
    h = {"Authorization": f"Bearer {token}"}
    if tenant:
        h["X-Tenant-Id"] = str(tenant)
    return h


def test_lists_only_own_tenant_locations(
    api: TestClient, world: World, make_token: MakeToken
) -> None:
    token = make_token(sub=str(world.user_a))
    res = api.get("/api/locations", headers=headers(token, world.tenant_a))
    assert res.status_code == 200
    ids = {loc["id"] for loc in res.json()}
    assert str(world.location_a) in ids
    assert str(world.location_b) not in ids


def test_other_tenant_header_is_forbidden(
    api: TestClient, world: World, make_token: MakeToken
) -> None:
    token = make_token(sub=str(world.user_a))
    for method in ("get", "post"):
        res = api.request(
            method, "/api/locations", headers=headers(token, world.tenant_b), json={"name": "x"}
        )
        assert res.status_code == 403
    res = api.get("/api/locations", headers=headers(token, uuid.uuid4()))
    assert res.status_code == 403


def test_tenant_header_is_required(api: TestClient, world: World, make_token: MakeToken) -> None:
    token = make_token(sub=str(world.user_a))
    assert api.get("/api/locations", headers=headers(token)).status_code == 422


def test_created_location_belongs_to_header_tenant(
    api: TestClient, world: World, make_token: MakeToken
) -> None:
    token_a = make_token(sub=str(world.user_a))
    token_b = make_token(sub=str(world.user_b))
    created = api.post(
        "/api/locations", headers=headers(token_a, world.tenant_a), json={"name": "A Harbour"}
    )
    assert created.status_code == 201
    new_id = created.json()["id"]
    seen_by_b = api.get("/api/locations", headers=headers(token_b, world.tenant_b)).json()
    assert new_id not in {loc["id"] for loc in seen_by_b}


def test_signup_to_first_tenant(api: TestClient, make_token: MakeToken) -> None:
    newcomer = uuid.uuid4()
    token = make_token(sub=str(newcomer), email="new@example.com")

    me = api.get("/api/me", headers=headers(token)).json()
    assert me["tenants"] == []

    slug = f"new-{uuid.uuid4().hex[:8]}"
    res = api.post("/api/tenants", headers=headers(token), json={"name": "New Salon", "slug": slug})
    assert res.status_code == 201
    tenant = res.json()
    assert tenant["role"] == "owner"

    me = api.get("/api/me", headers=headers(token)).json()
    assert [t["slug"] for t in me["tenants"]] == [slug]

    res = api.get("/api/locations", headers=headers(token, uuid.UUID(tenant["id"])))
    assert res.status_code == 200
    assert res.json() == []

    dup = api.post("/api/tenants", headers=headers(token), json={"name": "Again", "slug": slug})
    assert dup.status_code == 409


def test_invalid_slug_is_rejected(api: TestClient, make_token: MakeToken) -> None:
    token = make_token()
    res = api.post("/api/tenants", headers=headers(token), json={"name": "X", "slug": "Bad Slug!"})
    assert res.status_code == 422
