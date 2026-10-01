"""Module contract and discovery (ARCHITECTURE.md §2).

A module is a Python package that exposes a `ConfluoModule` object under the
`confluo.modules` entry point group. Only the parts needed so far are in the
contract; jobs, agent tools and migrations are added by the cards that need them.
"""

from dataclasses import dataclass, field
from importlib.metadata import entry_points
from typing import Protocol, runtime_checkable

from fastapi import APIRouter

from confluo_core.permissions import Permission

ENTRY_POINT_GROUP = "confluo.modules"


@dataclass(frozen=True)
class NavItem:
    key: str
    label: str
    href: str


@dataclass(frozen=True)
class ModuleManifest:
    """What the dashboard needs to render a module (nav items, later widgets and settings)."""

    nav: list[NavItem] = field(default_factory=list)


@runtime_checkable
class ConfluoModule(Protocol):
    key: str
    version: str
    depends_on: list[str]
    # Permission keys must start with the module key, e.g. "crm.inbox.takeover".
    permissions: list[Permission]

    def routers(self) -> list[APIRouter]: ...

    def dashboard_manifest(self) -> ModuleManifest: ...


class ModuleError(RuntimeError):
    pass


def discover_modules() -> dict[str, ConfluoModule]:
    """Load installed modules, ordered so every module comes after its dependencies."""
    found: dict[str, ConfluoModule] = {}
    for ep in entry_points(group=ENTRY_POINT_GROUP):
        module = ep.load()
        if not isinstance(module, ConfluoModule):
            raise ModuleError(f"entry point {ep.name!r} does not implement ConfluoModule")
        if module.key != ep.name:
            raise ModuleError(f"entry point {ep.name!r} exposes module key {module.key!r}")
        foreign = [p.key for p in module.permissions if not p.key.startswith(f"{module.key}.")]
        if foreign:
            raise ModuleError(f"module {module.key!r} declares foreign permissions {foreign}")
        found[module.key] = module
    return _order_by_dependencies(found)


def _order_by_dependencies(modules: dict[str, ConfluoModule]) -> dict[str, ConfluoModule]:
    ordered: dict[str, ConfluoModule] = {}
    visiting: set[str] = set()

    def visit(key: str) -> None:
        if key in ordered or key == "core":
            return
        if key not in modules:
            raise ModuleError(f"missing module dependency {key!r}")
        if key in visiting:
            raise ModuleError(f"circular module dependency at {key!r}")
        visiting.add(key)
        for dep in modules[key].depends_on:
            visit(dep)
        visiting.discard(key)
        ordered[key] = modules[key]

    for key in sorted(modules):
        visit(key)
    return ordered
