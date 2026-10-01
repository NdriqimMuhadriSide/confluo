from typing import Literal

from fastapi import APIRouter
from pydantic import BaseModel, Field

from confluo_core.modules import ModuleManifest, NavItem
from confluo_core.permissions import ALL, MANAGERS, Permission

router = APIRouter(tags=["crm"])


class CrmStatus(BaseModel):
    module: str
    status: str


@router.get("/status", operation_id="crmStatus")
async def status() -> CrmStatus:
    return CrmStatus(module="crm", status="ok")


class CrmConfig(BaseModel):
    """Per-tenant CRM settings (Settings → Modules)."""

    booking_mode: Literal["approval", "auto"] = Field(
        "approval",
        title="Booking mode",
        description="Approval: staff confirm AI bookings. Auto: confirmed immediately.",
    )
    reminder_hours_before: int = Field(
        24, ge=1, le=168, title="Reminder", description="Hours before the appointment."
    )
    ai_enabled: bool = Field(
        True, title="AI answers customers", description="Off: every message goes to staff."
    )


class CrmModule:
    key = "crm"
    version = "0.1.0"
    depends_on: list[str] = ["core"]
    config_schema = CrmConfig
    enabled_by_default = True
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
                NavItem("inbox", "Inbox", "/inbox", permission="crm.inbox.view"),
                NavItem("calendar", "Calendar", "/calendar"),
                NavItem("customers", "Customers", "/customers"),
                NavItem("knowledge", "Knowledge base", "/knowledge", permission="crm.kb.edit"),
            ]
        )


module = CrmModule()
