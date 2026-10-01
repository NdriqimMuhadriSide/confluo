from fastapi import APIRouter
from pydantic import BaseModel

from confluo_core.modules import ModuleManifest, NavItem
from confluo_core.permissions import ALL, MANAGERS, Permission

router = APIRouter(tags=["crm"])


class CrmStatus(BaseModel):
    module: str
    status: str


@router.get("/status", operation_id="crmStatus")
async def status() -> CrmStatus:
    return CrmStatus(module="crm", status="ok")


class CrmModule:
    key = "crm"
    version = "0.1.0"
    depends_on: list[str] = ["core"]
    permissions = [
        Permission("crm.inbox.view", "Read customer conversations", ALL),
        Permission("crm.inbox.takeover", "Take over a conversation from the AI", ALL),
        Permission("crm.kb.edit", "Edit the knowledge base", MANAGERS),
        Permission("crm.settings.manage", "Configure channels, services and booking", MANAGERS),
    ]

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
