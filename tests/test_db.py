"""Tests that need a real Postgres. CI provides one as a service container; locally
`make test` points at the Supabase stack (`make db`). Skipped when no URL is set."""

import os

import pytest
from fastapi.testclient import TestClient

from confluo_api.main import create_app
from confluo_core.settings import Settings

DATABASE_URL = os.environ.get("CONFLUO_TEST_DATABASE_URL")

pytestmark = pytest.mark.skipif(not DATABASE_URL, reason="CONFLUO_TEST_DATABASE_URL not set")


def test_health_is_ok_with_database() -> None:
    settings = Settings(database_url=DATABASE_URL)
    with TestClient(create_app(settings)) as c:
        assert c.get("/health").json() == {"status": "ok", "database": True}
