"""Roles and permissions (RBAC).

Roles are fixed: owner, admin, staff. Permissions are strings like
`crm.inbox.takeover`, declared by core and by each module together with the roles
that get them by default. Tenant-editable role/permission mappings can come later
(ARCHITECTURE.md §3, `role_permission`); until then the defaults are the mapping.

The API checks permissions with `requires(...)`. The few rules that guard against
privilege escalation (who may change members and the owner role) are also enforced
in the database, see migration 0002.
"""

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Literal

Role = Literal["owner", "admin", "staff"]
ROLES: tuple[Role, ...] = ("owner", "admin", "staff")

ALL: frozenset[Role] = frozenset(ROLES)
MANAGERS: frozenset[Role] = frozenset({"owner", "admin"})


@dataclass(frozen=True)
class Permission:
    key: str
    description: str
    roles: frozenset[Role]


CORE_PERMISSIONS = [
    Permission("core.members.view", "See the team and their roles", ALL),
    Permission("core.members.manage", "Invite, change roles of and remove members", MANAGERS),
    Permission("core.tenant.manage", "Edit business details", MANAGERS),
    Permission("core.locations.manage", "Add and edit locations", MANAGERS),
    Permission("core.modules.manage", "Turn modules on or off and change their settings", MANAGERS),
    Permission("core.audit.view", "See the audit log", MANAGERS),
    Permission("core.system.manage", "See failed background jobs and retry them", MANAGERS),
    Permission("core.ai_trace.view", "See why the AI did something", ALL),
]


class PermissionRegistry:
    def __init__(self, permissions: Iterable[Permission]) -> None:
        self._by_key: dict[str, Permission] = {}
        for p in permissions:
            if p.key in self._by_key:
                raise ValueError(f"permission {p.key!r} declared twice")
            self._by_key[p.key] = p

    def __contains__(self, key: str) -> bool:
        return key in self._by_key

    def all(self) -> list[Permission]:
        return sorted(self._by_key.values(), key=lambda p: p.key)

    def for_role(self, role: str) -> frozenset[str]:
        return frozenset(k for k, p in self._by_key.items() if role in p.roles)

    def allows(self, role: str, key: str) -> bool:
        if key not in self._by_key:
            raise KeyError(f"unknown permission {key!r}")
        return role in self._by_key[key].roles
