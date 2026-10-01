"""The current tenant, its members and invitations (RBAC card)."""

import logging
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from psycopg import errors
from psycopg.rows import class_row
from pydantic import BaseModel, EmailStr

from confluo_core.auth_admin import AuthAdmin, InviteOutcome
from confluo_core.deps import Tenant, TenantContext, requires
from confluo_core.permissions import Role

log = logging.getLogger("confluo.members")
router = APIRouter()

ViewMembers = Annotated[TenantContext, Depends(requires("core.members.view"))]
ManageMembers = Annotated[TenantContext, Depends(requires("core.members.manage"))]


class CurrentTenant(BaseModel):
    id: UUID
    name: str
    slug: str
    role: Role
    permissions: list[str]


class Member(BaseModel):
    user_id: UUID
    email: str
    name: str | None
    role: Role
    status: str


class RoleIn(BaseModel):
    role: Role


class InvitationIn(BaseModel):
    email: EmailStr
    role: Role = "staff"


class Invitation(BaseModel):
    id: UUID
    email: str
    role: Role
    expires_at: str


class InvitationCreated(Invitation):
    email_outcome: InviteOutcome


def _forbidden(detail: str) -> HTTPException:
    return HTTPException(status.HTTP_403_FORBIDDEN, detail)


@router.get("/api/tenant", tags=["tenant"], operation_id="getCurrentTenant")
async def current_tenant(tenant: Tenant) -> CurrentTenant:
    cur = await tenant.conn.execute(
        "select name, slug from tenant where id = %s", (tenant.tenant_id,)
    )
    row = await cur.fetchone()
    assert row is not None
    return CurrentTenant(
        id=tenant.tenant_id,
        name=row[0],
        slug=row[1],
        role=tenant.role,
        permissions=sorted(tenant.permissions),
    )


@router.get("/api/members", tags=["members"], operation_id="listMembers")
async def list_members(tenant: ViewMembers) -> list[Member]:
    cur = tenant.conn.cursor(row_factory=class_row(Member))
    await cur.execute(
        "select m.user_id, u.email, u.name, m.role, m.status"
        " from tenant_member m join app_user u on u.id = m.user_id"
        " where m.tenant_id = %s"
        " order by case m.role when 'owner' then 0 when 'admin' then 1 else 2 end, u.email",
        (tenant.tenant_id,),
    )
    return await cur.fetchall()


async def _target_role(tenant: TenantContext, user_id: UUID) -> str:
    cur = await tenant.conn.execute("select role from tenant_member where user_id = %s", (user_id,))
    row = await cur.fetchone()
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such member")
    return str(row[0])


def _check_owner_rules(tenant: TenantContext, *roles: str) -> None:
    # Mirrors the database policy, to return a clear 403 instead of "0 rows".
    if "owner" in roles and tenant.role != "owner":
        raise _forbidden("Only an owner can grant or change the owner role")


@router.patch("/api/members/{user_id}", tags=["members"], operation_id="changeMemberRole")
async def change_role(user_id: UUID, body: RoleIn, tenant: ManageMembers) -> Member:
    current = await _target_role(tenant, user_id)
    _check_owner_rules(tenant, current, body.role)
    try:
        async with tenant.conn.transaction():
            await tenant.conn.execute(
                "update tenant_member set role = %s where tenant_id = %s and user_id = %s",
                (body.role, tenant.tenant_id, user_id),
            )
    except errors.CheckViolation:
        raise HTTPException(
            status.HTTP_409_CONFLICT, "A business needs at least one owner"
        ) from None
    cur = tenant.conn.cursor(row_factory=class_row(Member))
    await cur.execute(
        "select m.user_id, u.email, u.name, m.role, m.status"
        " from tenant_member m join app_user u on u.id = m.user_id"
        " where m.tenant_id = %s and m.user_id = %s",
        (tenant.tenant_id, user_id),
    )
    member = await cur.fetchone()
    assert member is not None
    return member


@router.delete(
    "/api/members/{user_id}",
    tags=["members"],
    operation_id="removeMember",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def remove_member(user_id: UUID, tenant: ManageMembers) -> Response:
    current = await _target_role(tenant, user_id)
    _check_owner_rules(tenant, current)
    try:
        async with tenant.conn.transaction():
            await tenant.conn.execute(
                "delete from tenant_member where tenant_id = %s and user_id = %s",
                (tenant.tenant_id, user_id),
            )
    except errors.CheckViolation:
        raise HTTPException(
            status.HTTP_409_CONFLICT, "A business needs at least one owner"
        ) from None
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/api/invitations", tags=["members"], operation_id="listInvitations")
async def list_invitations(tenant: ManageMembers) -> list[Invitation]:
    cur = await tenant.conn.execute(
        "select id, email, role, expires_at from tenant_invitation"
        " where tenant_id = %s and accepted_at is null and revoked_at is null"
        " and expires_at > now() order by created_at",
        (tenant.tenant_id,),
    )
    return [
        Invitation(id=r[0], email=r[1], role=r[2], expires_at=r[3].isoformat())
        for r in await cur.fetchall()
    ]


@router.post(
    "/api/invitations",
    tags=["members"],
    operation_id="inviteMember",
    status_code=status.HTTP_201_CREATED,
)
async def invite_member(
    body: InvitationIn, tenant: ManageMembers, request: Request
) -> InvitationCreated:
    _check_owner_rules(tenant, body.role)
    email = body.email.lower()
    cur = await tenant.conn.execute(
        "select 1 from tenant_member m join app_user u on u.id = m.user_id"
        " where m.tenant_id = %s and lower(u.email) = %s and m.status = 'active'",
        (tenant.tenant_id, email),
    )
    if await cur.fetchone():
        raise HTTPException(status.HTTP_409_CONFLICT, "Already a member")
    try:
        async with tenant.conn.transaction():
            cur = await tenant.conn.execute(
                "insert into tenant_invitation (email, role) values (%s, %s)"
                " returning id, expires_at",
                (email, body.role),
            )
    except errors.UniqueViolation:
        raise HTTPException(status.HTTP_409_CONFLICT, "Already invited") from None
    row = await cur.fetchone()
    assert row is not None

    name_cur = await tenant.conn.execute(
        "select name from tenant where id = %s", (tenant.tenant_id,)
    )
    name_row = await name_cur.fetchone()
    auth_admin: AuthAdmin = request.app.state.auth_admin
    outcome = await auth_admin.invite(
        email, {"invited_to": name_row[0] if name_row else "", "invited_role": body.role}
    )
    log.info("invitation %s for tenant %s: %s", row[0], tenant.tenant_id, outcome)
    return InvitationCreated(
        id=row[0], email=email, role=body.role, expires_at=row[1].isoformat(), email_outcome=outcome
    )


@router.delete(
    "/api/invitations/{invitation_id}",
    tags=["members"],
    operation_id="revokeInvitation",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def revoke_invitation(invitation_id: UUID, tenant: ManageMembers) -> Response:
    cur = await tenant.conn.execute(
        "update tenant_invitation set revoked_at = now()"
        " where tenant_id = %s and id = %s and accepted_at is null and revoked_at is null",
        (tenant.tenant_id, invitation_id),
    )
    if cur.rowcount == 0:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such open invitation")
    return Response(status_code=status.HTTP_204_NO_CONTENT)
