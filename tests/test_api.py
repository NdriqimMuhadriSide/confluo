from fastapi.testclient import TestClient

from confluo_api.main import create_app
from confluo_core.settings import Settings


def client() -> TestClient:
    # Port 1 is never listening, so the database check fails fast without a real DB.
    settings = Settings(database_url="postgresql://x:x@127.0.0.1:1/x")
    return TestClient(create_app(settings))


def test_health_reports_database_state() -> None:
    with client() as c:
        body = c.get("/health").json()
    assert body == {"status": "degraded", "database": False}


def test_manifest_lists_crm() -> None:
    with client() as c:
        mods = c.get("/api/me/manifest").json()["modules"]
    assert [m["key"] for m in mods] == ["crm"]
    assert {n["key"] for n in mods[0]["nav"]} >= {"inbox", "calendar"}


def test_module_router_is_mounted() -> None:
    with client() as c:
        assert c.get("/api/crm/status").json() == {"module": "crm", "status": "ok"}
