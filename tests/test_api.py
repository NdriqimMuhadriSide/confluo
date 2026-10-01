from fastapi.testclient import TestClient


def test_health_is_public_and_reports_database_state(client: TestClient) -> None:
    assert client.get("/health").json() == {"status": "degraded", "database": False}


def test_module_routes_require_auth(client: TestClient) -> None:
    assert client.get("/api/crm/status").status_code == 401
    assert client.get("/api/me/manifest").status_code == 401


def test_client_ip_is_only_recorded_when_it_is_an_ip() -> None:
    from starlette.requests import Request

    from confluo_core.deps import client_ip

    def req(host: str | None) -> Request:
        return Request({"type": "http", "client": (host, 1) if host else None, "headers": []})

    assert client_ip(req("203.0.113.7")) == "203.0.113.7"
    assert client_ip(req("2001:db8::1")) == "2001:db8::1"
    assert client_ip(req("testclient")) is None
    assert client_ip(req(None)) is None
