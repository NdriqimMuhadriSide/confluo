from fastapi import APIRouter

from confluo_core.modules import ModuleError, ModuleManifest, discover_modules
from confluo_core.modules import _order_by_dependencies as order
from confluo_core.permissions import Permission


class Fake:
    version = "0"
    permissions: list[Permission] = []

    def __init__(self, key: str, depends_on: list[str]) -> None:
        self.key = key
        self.depends_on = depends_on

    def routers(self) -> list[APIRouter]:
        return []

    def dashboard_manifest(self) -> ModuleManifest:
        return ModuleManifest()


def test_crm_is_discovered() -> None:
    assert "crm" in discover_modules()


def test_dependencies_come_first() -> None:
    mods = {"finance": Fake("finance", ["crm"]), "crm": Fake("crm", ["core"])}
    assert list(order(mods)) == ["crm", "finance"]  # type: ignore[arg-type]


def test_missing_dependency_raises() -> None:
    try:
        order({"finance": Fake("finance", ["crm"])})
    except ModuleError as e:
        assert "crm" in str(e)
    else:
        raise AssertionError("expected ModuleError")
