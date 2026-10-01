"""Industry presets: catalog, applying each preset in each language, contents."""

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

LANGS = ["en", "nl", "fr", "de", "sq"]
PRESETS = ["salon", "garage", "physio", "generic"]


@pytest.fixture
def api(world: World, signing_key: ec.EllipticCurvePrivateKey) -> Iterator[TestClient]:
    settings = Settings(database_url=world.app_url)
    verifier = TokenVerifier(settings, jwks_client=StaticJWKS(signing_key.public_key()))
    with TestClient(create_app(settings, token_verifier=verifier)) as c:
        yield c


@pytest.fixture
def new_business(api: TestClient, make_token: MakeToken) -> Any:
    def create(preset: str | None, language: str = "en") -> dict[str, Any]:
        user = uuid.uuid4()
        h = {"Authorization": f"Bearer {make_token(sub=str(user), email=f'{user.hex[:6]}@x.be')}"}
        body: dict[str, Any] = {
            "name": "Preset test",
            "slug": f"p-{user.hex[:10]}",
            "language": language,
        }
        if preset:
            body["preset"] = preset
        res = api.post("/api/tenants", headers=h, json=body)
        assert res.status_code == 201, res.text
        tenant = res.json()["id"]
        return {"tenant": tenant, "h": h | {"X-Tenant-Id": tenant}}

    return create


def test_catalog_lists_four_presets_in_all_languages(
    api: TestClient, make_token: MakeToken
) -> None:
    h = {"Authorization": f"Bearer {make_token()}"}
    presets = api.get("/api/presets", headers=h).json()
    assert [p["key"] for p in presets] == PRESETS
    for p in presets:
        assert set(p["name_i18n"]) == set(LANGS) and set(p["description_i18n"]) == set(LANGS)


@pytest.mark.parametrize("preset", PRESETS)
def test_every_preset_applies_in_every_language(
    new_business: Any, api: TestClient, preset: str
) -> None:
    for lang in LANGS:
        biz = new_business(preset, lang)
        services = api.get("/api/crm/services", headers=biz["h"]).json()
        assert services and all(set(s["name_i18n"]) == set(LANGS) for s in services)
        kb = api.get("/api/crm/knowledge", headers=biz["h"]).json()
        assert kb and {i["language"] for i in kb} == {lang}
        assert all(i["status"] == "draft" for i in kb)  # the owner reviews and publishes


def test_garage_preset_contents(new_business: Any, api: TestClient, world: World) -> None:
    biz = new_business("garage", "nl")
    h = biz["h"]
    services = {s["name_i18n"]["nl"]: s for s in api.get("/api/crm/services", headers=h).json()}
    assert set(services) == {"Olieverversing", "Bandenwissel", "Keuringsvoorbereiding", "Diagnose"}
    fields = {f["id"]: f["key"] for f in api.get("/api/fields", headers=h).json()}
    tyres = services["Bandenwissel"]
    assert {fields[f["field_id"]] for f in tyres["fields"]} == {"phone", "licence_plate"}
    assert all(f["required"] for f in tyres["fields"])
    assert (tyres["duration_min"], tyres["buffer_after_min"], tyres["price_cents"]) == (
        45,
        15,
        6000,
    )
    resources = {r["name"]: r for r in api.get("/api/crm/resources", headers=h).json()}
    assert {r: resources[r]["kind"] for r in resources} == {
        "Mecanicien 1": "staff",
        "Brug 1": "room",
    }
    assert tyres["resource_ids"] == [resources["Mecanicien 1"]["id"]]
    [location] = api.get("/api/locations", headers=h).json()
    assert location["opening_hours"]["mon"] == [
        {"start": "08:00:00", "end": "12:00:00"},
        {"start": "13:00:00", "end": "17:30:00"},
    ]
    schedule = api.get(
        f"/api/crm/resources/{resources['Mecanicien 1']['id']}/schedule", headers=h
    ).json()
    assert len(schedule["rules"]) == 10  # 5 days x 2 shifts
    titles = {i["title"] for i in api.get("/api/crm/knowledge", headers=h).json()}
    assert "Openingsuren" in titles and "Kan ik wachten tijdens de herstelling?" in titles
    [crm] = api.get("/api/modules", headers=h).json()
    assert crm["config"]["booking_mode"] == "approval"
    with psycopg.connect(world.owner_url) as conn:
        assert conn.execute(
            "select default_locale from tenant where id = %s", (biz["tenant"],)
        ).fetchone() == ("nl",)


def test_physio_marks_health_data_sensitive(new_business: Any, api: TestClient) -> None:
    biz = new_business("physio", "fr")
    fields = {f["key"]: f for f in api.get("/api/fields", headers=biz["h"]).json()}
    assert fields["complaint"]["pii_level"] == "sensitive"
    assert fields["referral"]["pii_level"] == "sensitive"
    assert fields["date_of_birth"]["entity"] == "customer"
    [crm] = api.get("/api/modules", headers=biz["h"]).json()
    assert crm["config"]["booking_mode"] == "approval"
    hours = next(
        i for i in api.get("/api/crm/knowledge", headers=biz["h"]).json() if i["kind"] == "hours"
    )
    assert hours["title"] == "Heures d'ouverture" and "Lundi: 08:00–19:00" in hours["body"]


def test_no_preset_starts_empty_and_unknown_preset_is_rejected(
    new_business: Any, api: TestClient, make_token: MakeToken
) -> None:
    biz = new_business(None)
    assert api.get("/api/crm/services", headers=biz["h"]).json() == []
    h = {"Authorization": f"Bearer {make_token()}"}
    res = api.post(
        "/api/tenants",
        headers=h,
        json={"name": "X", "slug": f"x-{uuid.uuid4().hex[:8]}", "preset": "bakery"},
    )
    assert res.status_code == 422
