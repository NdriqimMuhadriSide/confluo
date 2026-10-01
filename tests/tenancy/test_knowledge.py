"""Knowledge base: editor, publish → embedding job, hybrid search, isolation."""

import uuid
from collections.abc import AsyncIterator, Iterator
from typing import Any

import psycopg
import pytest
from cryptography.hazmat.primitives.asymmetric import ec
from fastapi.testclient import TestClient

from confluo_api.main import create_app
from confluo_core.auth import TokenVerifier
from confluo_core.db import make_pool
from confluo_core.job_app import build_job_app
from confluo_core.jobs import LLM_KEY, run_jobs_once, worker_context
from confluo_core.llm import LLMGateway
from confluo_core.llm.fake_provider import FakeProvider
from confluo_core.modules import discover_modules
from confluo_core.settings import Settings
from tests.conftest import MakeToken, StaticJWKS
from tests.tenancy.conftest import World


def settings(world: World) -> Settings:
    return Settings(database_url=world.app_url, llm_embedding="fake:voyage-3.5")


@pytest.fixture
def shop(world: World) -> dict[str, uuid.UUID]:
    ids = {k: uuid.uuid4() for k in ("tenant", "owner", "staff")}
    with psycopg.connect(world.owner_url, autocommit=True) as conn:
        conn.execute(
            "insert into tenant (id, name, slug) values (%s, 'KB', %s)",
            (ids["tenant"], f"kb-{ids['tenant'].hex[:10]}"),
        )
        for who in ("owner", "staff"):
            conn.execute(
                "insert into app_user (id, email) values (%s, %s)", (ids[who], f"{who}@kb.be")
            )
            conn.execute(
                "insert into tenant_member (tenant_id, user_id, role) values (%s, %s, %s)",
                (ids["tenant"], ids[who], who),
            )
    return ids


@pytest.fixture
def api(world: World, signing_key: ec.EllipticCurvePrivateKey) -> Iterator[TestClient]:
    s = settings(world)
    verifier = TokenVerifier(s, jwks_client=StaticJWKS(signing_key.public_key()))
    with TestClient(
        create_app(s, token_verifier=verifier, llm_providers={"fake": FakeProvider()})
    ) as c:
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


@pytest.fixture
async def run_jobs(world: World) -> AsyncIterator[Any]:
    pool = make_pool(world.app_url, max_size=4)
    await pool.open()
    app = build_job_app(discover_modules())
    llm = LLMGateway(settings(world), pool, {"fake": FakeProvider()})
    async with app.open_async(pool):
        ctx = worker_context(pool, **{LLM_KEY: llm})

        async def run() -> None:
            await run_jobs_once(app, ctx)

        yield run
    await pool.close()


ITEMS = [
    ("faq", "Parking", "Free parking behind the salon, entrance via the Kouter.", "en"),
    ("faq", "Payment", "We accept Bancontact, Visa, Mastercard and cash.", "en"),
    ("hours", "Openingsuren", "Maandag tot vrijdag 9u tot 18u, zaterdag 9u tot 16u.", "nl"),
]


async def _publish_all(call: Any, run_jobs: Any) -> dict[str, str]:
    ids = {}
    for kind, title, body, lang in ITEMS:
        item = call(
            "owner",
            "post",
            "/api/crm/knowledge",
            json={"kind": kind, "title": title, "body": body, "language": lang},
        ).json()
        assert item["status"] == "draft"
        published = call("owner", "post", f"/api/crm/knowledge/{item['id']}/publish").json()
        assert published["status"] == "indexing"
        ids[title] = item["id"]
    await run_jobs()
    return ids


async def test_owner_writes_and_publishing_indexes(call: Any, run_jobs: Any, world: World) -> None:
    assert (
        call(
            "staff",
            "post",
            "/api/crm/knowledge",
            json={"kind": "faq", "title": "x", "body": "y", "language": "en"},
        ).status_code
        == 403
    )
    ids = await _publish_all(call, run_jobs)
    items = {i["title"]: i for i in call("owner", "get", "/api/crm/knowledge").json()}
    assert {t: (i["status"], i["chunks"]) for t, i in items.items()} == {
        "Parking": ("live", 1),
        "Payment": ("live", 1),
        "Openingsuren": ("live", 1),
    }
    with psycopg.connect(world.owner_url) as conn:
        model = conn.execute(
            "select distinct embedding_model from crm_knowledge_chunk where knowledge_item_id = %s",
            (ids["Parking"],),
        ).fetchall()
    assert model == [("voyage-3.5",)]

    # Editing live content re-indexes it.
    edited = call(
        "owner",
        "put",
        f"/api/crm/knowledge/{ids['Payment']}",
        json={
            "kind": "faq",
            "title": "Payment",
            "body": "Only card payments, no cash.",
            "language": "en",
        },
    ).json()
    assert edited["status"] == "indexing"
    await run_jobs()
    hits = call("staff", "get", "/api/crm/knowledge/search", params={"q": "cash"}).json()
    assert hits[0]["content"] == "Payment\nOnly card payments, no cash."


async def test_hybrid_search_finds_the_right_item(call: Any, run_jobs: Any) -> None:
    await _publish_all(call, run_jobs)
    hits = call(
        "staff", "get", "/api/crm/knowledge/search", params={"q": "Is there free parking?"}
    ).json()
    assert hits[0]["title"] == "Parking"
    assert hits[0]["vector_rank"] == 1 and hits[0]["text_rank"] == 1
    nl = call(
        "staff", "get", "/api/crm/knowledge/search", params={"q": "zaterdag open", "language": "nl"}
    ).json()
    assert [h["title"] for h in nl] == ["Openingsuren"]
    # Vector-only match still ranks (no full-text hit for this wording).
    hits = call(
        "staff", "get", "/api/crm/knowledge/search", params={"q": "visa mastercard?"}
    ).json()
    assert hits[0]["title"] == "Payment"


async def test_unpublished_and_other_tenants_are_not_searched(
    call: Any, run_jobs: Any, api: TestClient, make_token: MakeToken, world: World
) -> None:
    ids = await _publish_all(call, run_jobs)
    off = call("owner", "post", f"/api/crm/knowledge/{ids['Parking']}/unpublish").json()
    assert (off["status"], off["chunks"]) == ("draft", 0)
    hits = call("staff", "get", "/api/crm/knowledge/search", params={"q": "free parking"}).json()
    assert "Parking" not in [h["title"] for h in hits]
    other = {
        "Authorization": f"Bearer {make_token(sub=str(world.user_a))}",
        "X-Tenant-Id": str(world.tenant_a),
    }
    assert "Payment" not in [
        h["title"]
        for h in api.get("/api/crm/knowledge/search", headers=other, params={"q": "visa"}).json()
    ]
    assert call("owner", "delete", f"/api/crm/knowledge/{ids['Payment']}").status_code == 204


async def test_chunks_of_another_model_are_ignored(call: Any, run_jobs: Any, world: World) -> None:
    ids = await _publish_all(call, run_jobs)
    with psycopg.connect(world.owner_url, autocommit=True) as conn:
        conn.execute(
            "update crm_knowledge_chunk set embedding_model = 'old-model' where knowledge_item_id = %s",
            (ids["Parking"],),
        )
    hits = call("staff", "get", "/api/crm/knowledge/search", params={"q": "free parking"}).json()
    assert "Parking" not in [h["title"] for h in hits]
