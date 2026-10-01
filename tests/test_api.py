from fastapi.testclient import TestClient

from tests.conftest import MakeToken


def auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def test_health_is_public_and_reports_database_state(client: TestClient) -> None:
    assert client.get("/health").json() == {"status": "degraded", "database": False}


def test_manifest_lists_crm(client: TestClient, make_token: MakeToken) -> None:
    mods = client.get("/api/me/manifest", headers=auth(make_token())).json()["modules"]
    assert [m["key"] for m in mods] == ["crm"]
    assert {n["key"] for n in mods[0]["nav"]} >= {"inbox", "calendar"}


def test_module_router_is_mounted_behind_auth(client: TestClient, make_token: MakeToken) -> None:
    assert client.get("/api/crm/status").status_code == 401
    res = client.get("/api/crm/status", headers=auth(make_token()))
    assert res.json() == {"module": "crm", "status": "ok"}
