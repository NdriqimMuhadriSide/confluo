from fastapi import APIRouter

from confluo_core.modules import ModuleManifest, NavItem

router = APIRouter(tags=["crm"])


@router.get("/status")
async def status() -> dict[str, str]:
    return {"module": "crm", "status": "ok"}


class CrmModule:
    key = "crm"
    version = "0.1.0"
    depends_on: list[str] = ["core"]

    def routers(self) -> list[APIRouter]:
        return [router]

    def dashboard_manifest(self) -> ModuleManifest:
        return ModuleManifest(
            nav=[
                NavItem(key="inbox", label="Inbox", href="/inbox"),
                NavItem(key="calendar", label="Calendar", href="/calendar"),
                NavItem(key="customers", label="Customers", href="/customers"),
            ]
        )


module = CrmModule()
