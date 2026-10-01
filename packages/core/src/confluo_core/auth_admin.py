"""Server-side calls to Supabase Auth's admin API (needs the secret key)."""

import json
import logging
import urllib.error
import urllib.request
from typing import Any, Literal, Protocol

from fastapi.concurrency import run_in_threadpool

from confluo_core.settings import Settings

log = logging.getLogger("confluo.auth_admin")

InviteOutcome = Literal["email_sent", "existing_user", "not_configured"]


class AuthAdmin(Protocol):
    async def invite(self, email: str, data: dict[str, Any]) -> InviteOutcome: ...


class SupabaseAuthAdmin:
    def __init__(self, settings: Settings) -> None:
        self._url = f"{settings.supabase_auth_url}/invite"
        self._key = settings.supabase_secret_key

    def _invite_sync(self, email: str, data: dict[str, Any]) -> InviteOutcome:
        if self._key is None:
            log.warning("CONFLUO_SUPABASE_SECRET_KEY not set; no invitation email sent")
            return "not_configured"
        key = self._key.get_secret_value()
        req = urllib.request.Request(
            self._url,
            data=json.dumps({"email": email, "data": data}).encode(),
            headers={
                "apikey": key,
                "Authorization": f"Bearer {key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=10):
                return "email_sent"
        except urllib.error.HTTPError as e:
            body = e.read().decode(errors="replace")
            # GoTrue answers 422 email_exists for accounts that already exist; they see
            # the invitation in the dashboard at their next sign-in.
            if e.code == 422 and "email_exists" in body:
                return "existing_user"
            raise RuntimeError(f"Supabase invite failed: HTTP {e.code} {body[:200]}") from e

    async def invite(self, email: str, data: dict[str, Any]) -> InviteOutcome:
        return await run_in_threadpool(self._invite_sync, email, data)
