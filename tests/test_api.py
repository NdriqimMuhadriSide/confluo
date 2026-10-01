from fastapi.testclient import TestClient


def test_health_is_public_and_reports_database_state(client: TestClient) -> None:
    assert client.get("/health").json() == {"status": "degraded", "database": False}


def test_module_routes_require_auth(client: TestClient) -> None:
    assert client.get("/api/crm/status").status_code == 401
    assert client.get("/api/me/manifest").status_code == 401
