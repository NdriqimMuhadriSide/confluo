"""Roles, permission guards and invitations, through the API and directly in the DB."""

import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any

import psycopg
import pytest
from cryptography.hazmat.primitives.asymmetric import ec
from fastapi.testclient import TestClient
from psycopg import errors
from psycopg_pool import AsyncConnectionPool

from confluo_api.main import create_app
from confluo_core.auth import TokenVerifier
from confluo_core.auth_admin import InviteOutcome
from confluo_core.tenancy import tenant_transaction
from tests.conftest import MakeToken, StaticJWKS
from tests.tenancy.conftest import World


class FakeAuthAdmin:
    def __init__(self) -> None:
        self.sent: list[tuple[str, dict[str, Any]]] = []

    async def invite(self, email: str, data: dict[str, Any]) -> InviteOutcome:
        self.sent.append((email, data))
        return "email_sent"


@dataclass
class Team:
    tenant: uuid.UUID
    owner: uuid.UUID
    admin: uuid.UUID
    staff: uuid.UUID
    outsider: uuid.UUID

    def email(self, user: uuid.UUID) -> str:
        return f"{user}@example.com"


@pytest.fixture
def team(world: World) -> Team:
    """A fresh tenant with an owner, an admin and a staff member, plus an outsider."""
    t = Team(*(uuid.uuid4() for _ in range(5)))
    with psycopg.connect(world.owner_url, autocommit=True) as conn:
        conn.execute(
            "insert into tenant (id, name, slug) values (%s, 'Team', %s)",
            (t.tenant, f"team-{t.tenant.hex[:10]}"),
        )
        for user in (t.owner, t.admin, t.staff, t.outsider):
            conn.execute("insert into app_user (id, email) values (%s, %s)", (user, t.email(user)))
        for user, role in ((t.owner, "owner"), (t.admin, "admin"), (t.staff, "staff")):
            conn.execute(
                "insert into tenant_member (tenant_id, user_id, role) values (%s, %s, %s)",
                (t.tenant, user, role),
            )
    return t


@pytest.fixture
def mailer() -> FakeAuthAdmin:
    return FakeAuthAdmin()


@pytest.fixture
def api(
    world: World, signing_key: ec.EllipticCurvePrivateKey, mailer: FakeAuthAdmin
) -> Iterator[TestClient]:
    from confluo_core.settings import Settings

    settings = Settings(database_url=world.app_url)
    verifier = TokenVerifier(settings, jwks_client=StaticJWKS(signing_key.public_key()))
    with TestClient(create_app(settings, token_verifier=verifier, auth_admin=mailer)) as c:
        yield c


class As:
    """Requests as one user, in the team's tenant."""

    def __init__(self, api: TestClient, make_token: MakeToken, team: Team, user: uuid.UUID):
        self.api = api
        self.headers = {
            "Authorization": f"Bearer {make_token(sub=str(user), email=team.email(user))}",
            "X-Tenant-Id": str(team.tenant),
        }

    def __getattr__(self, method: str) -> Any:
        return lambda url, **kw: self.api.request(method, url, headers=self.headers, **kw)


@pytest.fixture
def as_user(api: TestClient, make_token: MakeToken, team: Team) -> Any:
    return lambda user: As(api, make_token, team, user)


# --- Permissions ------------------------------------------------------------------


def test_current_tenant_reports_role_and_permissions(as_user: Any, team: Team) -> None:
    staff = as_user(team.staff).get("/api/tenant").json()
    assert staff["role"] == "staff"
    assert "core.members.view" in staff["permissions"]
    assert "core.members.manage" not in staff["permissions"]
    assert "crm.inbox.takeover" in staff["permissions"]
    owner = as_user(team.owner).get("/api/tenant").json()
    assert "core.members.manage" in owner["permissions"]


def test_everyone_can_see_the_team(as_user: Any, team: Team) -> None:
    members = as_user(team.staff).get("/api/members").json()
    assert {m["role"] for m in members} == {"owner", "admin", "staff"}


def test_staff_cannot_manage(as_user: Any, team: Team) -> None:
    staff = as_user(team.staff)
    assert staff.post("/api/invitations", json={"email": "x@example.com"}).status_code == 403
    assert staff.get("/api/invitations").status_code == 403
    assert staff.patch(f"/api/members/{team.admin}", json={"role": "staff"}).status_code == 403
    assert staff.delete(f"/api/members/{team.admin}").status_code == 403
    assert staff.post("/api/locations", json={"name": "x"}).status_code == 403


def test_admin_manages_staff_and_admins_but_not_owners(as_user: Any, team: Team) -> None:
    admin = as_user(team.admin)
    res = admin.patch(f"/api/members/{team.staff}", json={"role": "admin"})
    assert res.status_code == 200 and res.json()["role"] == "admin"
    assert admin.patch(f"/api/members/{team.staff}", json={"role": "owner"}).status_code == 403
    assert admin.patch(f"/api/members/{team.owner}", json={"role": "staff"}).status_code == 403
    assert admin.delete(f"/api/members/{team.owner}").status_code == 403
    assert (
        admin.post("/api/invitations", json={"email": "o@example.com", "role": "owner"}).status_code
        == 403
    )


def test_owner_can_promote_and_last_owner_is_protected(as_user: Any, team: Team) -> None:
    owner = as_user(team.owner)
    assert owner.patch(f"/api/members/{team.owner}", json={"role": "admin"}).status_code == 409
    assert owner.delete(f"/api/members/{team.owner}").status_code == 409
    assert owner.patch(f"/api/members/{team.admin}", json={"role": "owner"}).status_code == 200
    # With a second owner, stepping down is fine.
    assert owner.patch(f"/api/members/{team.owner}", json={"role": "admin"}).status_code == 200


def test_removed_member_loses_access(as_user: Any, team: Team) -> None:
    assert as_user(team.owner).delete(f"/api/members/{team.staff}").status_code == 204
    assert as_user(team.staff).get("/api/tenant").status_code == 403


def test_unknown_permission_fails_at_startup(world: World) -> None:
    from confluo_core import deps
    from confluo_core.settings import Settings

    deps.requires("core.does.not.exist")
    try:
        with pytest.raises(RuntimeError, match="core.does.not.exist"):
            create_app(Settings(database_url=world.app_url))
    finally:
        deps.REQUIRED_PERMISSIONS.discard("core.does.not.exist")


# --- Database-level guards (what a buggy endpoint could still not do) ----------------


async def test_db_staff_cannot_change_roles(pool: AsyncConnectionPool, team: Team) -> None:
    async with tenant_transaction(pool, team.tenant, team.staff) as conn:
        cur = await conn.execute(
            "update tenant_member set role = 'owner' where user_id = %s", (team.staff,)
        )
        assert cur.rowcount == 0
        cur = await conn.execute("delete from tenant_member where user_id = %s", (team.admin,))
        assert cur.rowcount == 0


async def test_db_admin_cannot_make_owners(pool: AsyncConnectionPool, team: Team) -> None:
    with pytest.raises(errors.InsufficientPrivilege):
        async with tenant_transaction(pool, team.tenant, team.admin) as conn:
            await conn.execute(
                "update tenant_member set role = 'owner' where user_id = %s", (team.staff,)
            )
    async with tenant_transaction(pool, team.tenant, team.admin) as conn:
        cur = await conn.execute("delete from tenant_member where user_id = %s", (team.owner,))
        assert cur.rowcount == 0


async def test_db_members_cannot_be_inserted_directly(
    pool: AsyncConnectionPool, team: Team
) -> None:
    with pytest.raises(errors.InsufficientPrivilege):
        async with tenant_transaction(pool, team.tenant, team.owner) as conn:
            await conn.execute(
                "insert into tenant_member (tenant_id, user_id, role) values (%s, %s, 'staff')",
                (team.tenant, team.outsider),
            )


async def test_db_staff_cannot_create_or_read_invitations(
    pool: AsyncConnectionPool, team: Team
) -> None:
    async with tenant_transaction(pool, team.tenant, team.staff) as conn:
        cur = await conn.execute("select count(*) from tenant_invitation")
        assert await cur.fetchone() == (0,)
    with pytest.raises(errors.InsufficientPrivilege):
        async with tenant_transaction(pool, team.tenant, team.staff) as conn:
            await conn.execute("insert into tenant_invitation (email) values ('x@example.com')")


# --- Invitations ------------------------------------------------------------------------


def _token_for(make_token: MakeToken, user: uuid.UUID, email: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {make_token(sub=str(user), email=email)}"}


def test_invite_and_accept(
    api: TestClient, as_user: Any, team: Team, make_token: MakeToken, mailer: FakeAuthAdmin
) -> None:
    invitee, email = uuid.uuid4(), f"new-{uuid.uuid4().hex[:8]}@example.com"
    res = as_user(team.admin).post(
        "/api/invitations", json={"email": email.upper(), "role": "staff"}
    )
    assert res.status_code == 201
    assert res.json()["email"] == email and res.json()["email_outcome"] == "email_sent"
    assert mailer.sent == [(email, {"invited_to": "Team", "invited_role": "staff"})]
    assert [i["email"] for i in as_user(team.owner).get("/api/invitations").json()] == [email]

    # Re-inviting the same email is a conflict; it's already open.
    assert as_user(team.owner).post("/api/invitations", json={"email": email}).status_code == 409

    headers = _token_for(make_token, invitee, email)
    me = api.get("/api/me", headers=headers).json()
    assert me["tenants"] == []
    [inv] = me["invitations"]
    assert inv["tenant_name"] == "Team" and inv["role"] == "staff"

    res = api.post(f"/api/me/invitations/{inv['id']}/accept", headers=headers)
    assert res.status_code == 200
    assert res.json()["role"] == "staff"

    me = api.get("/api/me", headers=headers).json()
    assert [t["id"] for t in me["tenants"]] == [str(team.tenant)]
    assert me["invitations"] == []
    # Accepting twice fails.
    assert api.post(f"/api/me/invitations/{inv['id']}/accept", headers=headers).status_code == 404


def test_invitation_cannot_be_accepted_by_another_email(
    api: TestClient, as_user: Any, team: Team, make_token: MakeToken
) -> None:
    email = f"target-{uuid.uuid4().hex[:8]}@example.com"
    inv = as_user(team.owner).post("/api/invitations", json={"email": email}).json()
    thief = _token_for(make_token, uuid.uuid4(), "thief@example.com")
    assert api.get("/api/me", headers=thief).json()["invitations"] == []
    assert api.post(f"/api/me/invitations/{inv['id']}/accept", headers=thief).status_code == 404


def test_revoked_and_expired_invitations_cannot_be_accepted(
    api: TestClient, as_user: Any, team: Team, make_token: MakeToken, world: World
) -> None:
    owner = as_user(team.owner)
    revoked_email = f"rev-{uuid.uuid4().hex[:8]}@example.com"
    revoked = owner.post("/api/invitations", json={"email": revoked_email}).json()
    assert owner.delete(f"/api/invitations/{revoked['id']}").status_code == 204
    headers = _token_for(make_token, uuid.uuid4(), revoked_email)
    assert (
        api.post(f"/api/me/invitations/{revoked['id']}/accept", headers=headers).status_code == 404
    )

    expired_email = f"exp-{uuid.uuid4().hex[:8]}@example.com"
    expired = owner.post("/api/invitations", json={"email": expired_email}).json()
    with psycopg.connect(world.owner_url, autocommit=True) as conn:
        conn.execute(
            "update tenant_invitation set expires_at = now() - interval '1 minute' where id = %s",
            (expired["id"],),
        )
    headers = _token_for(make_token, uuid.uuid4(), expired_email)
    assert api.get("/api/me", headers=headers).json()["invitations"] == []
    assert (
        api.post(f"/api/me/invitations/{expired['id']}/accept", headers=headers).status_code == 404
    )


def test_inviting_an_existing_member_is_a_conflict(as_user: Any, team: Team) -> None:
    res = as_user(team.owner).post("/api/invitations", json={"email": team.email(team.staff)})
    assert res.status_code == 409


def test_invitations_of_other_tenants_are_invisible(
    as_user: Any, team: Team, world: World, make_token: MakeToken, api: TestClient
) -> None:
    as_user(team.owner).post("/api/invitations", json={"email": "someone@example.com"})
    other_owner = {
        "Authorization": f"Bearer {make_token(sub=str(world.user_a), email='a@example.com')}",
        "X-Tenant-Id": str(world.tenant_a),
    }
    emails = [i["email"] for i in api.get("/api/invitations", headers=other_owner).json()]
    assert "someone@example.com" not in emails


# --- A user in several tenants ----------------------------------------------------------


@pytest.fixture
def second_tenant(world: World, team: Team) -> uuid.UUID:
    """Another tenant where the team's owner is staff."""
    other = uuid.uuid4()
    with psycopg.connect(world.owner_url, autocommit=True) as conn:
        conn.execute(
            "insert into tenant (id, name, slug) values (%s, 'Other', %s)",
            (other, f"other-{other.hex[:10]}"),
        )
        conn.execute(
            "insert into tenant_member (tenant_id, user_id, role) values (%s, %s, 'owner'),"
            " (%s, %s, 'staff')",
            (other, team.outsider, other, team.owner),
        )
    return other


async def test_db_inside_a_tenant_only_that_tenant_is_visible(
    pool: AsyncConnectionPool, team: Team, second_tenant: uuid.UUID
) -> None:
    async with tenant_transaction(pool, team.tenant, team.owner) as conn:
        cur = await conn.execute("select distinct tenant_id from tenant_member")
        assert await cur.fetchall() == [(team.tenant,)]
        cur = await conn.execute("select id from tenant")
        assert await cur.fetchall() == [(team.tenant,)]
    # Without a tenant (what /api/me does) the user sees all their memberships.
    async with pool.connection() as conn, conn.transaction():
        await conn.execute("select set_config('app.user_id', %s, true)", (str(team.owner),))
        cur = await conn.execute("select tenant_id, role from tenant_member order by role")
        assert set(await cur.fetchall()) == {(team.tenant, "owner"), (second_tenant, "staff")}


def test_members_list_has_no_rows_from_other_tenants(
    as_user: Any, team: Team, second_tenant: uuid.UUID
) -> None:
    members = as_user(team.owner).get("/api/members").json()
    assert sorted(m["user_id"] for m in members) == sorted(
        str(u) for u in (team.owner, team.admin, team.staff)
    )


def test_role_change_stays_in_current_tenant(
    as_user: Any, team: Team, second_tenant: uuid.UUID, world: World
) -> None:
    # The owner here is staff in the second tenant; promoting the admin to owner and
    # then the original owner stepping down must not touch the second tenant.
    owner = as_user(team.owner)
    assert owner.patch(f"/api/members/{team.admin}", json={"role": "owner"}).status_code == 200
    assert owner.patch(f"/api/members/{team.owner}", json={"role": "admin"}).status_code == 200
    with psycopg.connect(world.owner_url) as conn:
        row = conn.execute(
            "select role from tenant_member where tenant_id = %s and user_id = %s",
            (second_tenant, team.owner),
        ).fetchone()
    assert row == ("staff",)
