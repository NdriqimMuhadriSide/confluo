from typing import Literal

from fastapi import APIRouter
from psycopg import AsyncConnection
from pydantic import BaseModel, Field

from confluo_core.modules import ModuleManifest, NavItem
from confluo_core.permissions import ALL, MANAGERS, Permission
from confluo_crm import channels_api, knowledge_api, presets, setup_api
from confluo_crm.calendar import api as calendar_api
from confluo_crm.calendar import sync as calendar_sync
from confluo_crm.calendar.webhook import MicrosoftCalendarProvider
from confluo_crm.channels import web_public
from confluo_crm.channels.web import WebChatProvider
from confluo_crm.intake import brain
from confluo_crm.knowledge import crm_tasks

router = APIRouter(tags=["crm"])
setup_api.router.tags = ["crm-setup"]
knowledge_api.router.tags = ["crm-knowledge"]


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
    tasks = crm_tasks
    presets = presets.CATALOG
    permissions = [
        Permission("crm.inbox.view", "Read customer conversations", ALL),
        Permission("crm.inbox.takeover", "Take over a conversation from the AI", ALL),
        Permission("crm.kb.edit", "Edit the knowledge base", MANAGERS),
        Permission("crm.settings.manage", "Configure channels, services and booking", MANAGERS),
    ]

    def routers(self) -> list[APIRouter]:
        return [
            router,
            setup_api.router,
            knowledge_api.router,
            channels_api.router,
            calendar_api.router,
        ]

    def public_routers(self) -> list[APIRouter]:
        return [web_public.router]

    def webhook_providers(self) -> list[WebChatProvider | MicrosoftCalendarProvider]:
        return [WebChatProvider(), MicrosoftCalendarProvider()]

    async def apply_preset(
        self, conn: AsyncConnection, key: str, language: str, timezone: str
    ) -> None:
        await presets.apply_preset(conn, key, language, timezone)

    def dashboard_manifest(self) -> ModuleManifest:
        return ModuleManifest(
            nav=[
                NavItem("inbox", "Inbox", "/inbox", permission="crm.inbox.view"),
                NavItem("calendar", "Calendar", "/calendar"),
                NavItem("customers", "Customers", "/customers"),
                NavItem("knowledge", "Knowledge base", "/knowledge", permission="crm.kb.edit"),
            ]
        )


_ = calendar_sync  # registers the calendar jobs on crm_tasks

# Offline stand-in answers for the `fake` LLM provider (local dev without keys, CI).
brain.register()

module = CrmModule()
