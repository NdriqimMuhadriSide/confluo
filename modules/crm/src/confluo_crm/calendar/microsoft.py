"""Microsoft Graph for Outlook / Microsoft 365 calendars.

OAuth 2.0 authorization code flow with PKCE (delegated permissions
`Calendars.ReadWrite User.Read offline_access`). Busy time is read with
calendarView delta queries; Confluo's bookings are written as events; change
notifications (subscriptions) make a sync start within seconds of an Outlook change.

All HTTP goes through `GraphClient`, whose transport tests replace with a fake Graph
(`set_transport`).
"""

import base64
import hashlib
import json
import os
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlencode
from uuid import UUID

import httpx2
from cryptography.exceptions import InvalidTag

from confluo_core.secrets import seal, unseal
from confluo_core.settings import Settings

SCOPES = "offline_access User.Read Calendars.ReadWrite"
STATE_TTL = 15 * 60
BLOCKING = {"busy", "oof", "tentative"}  # showAs values that make a person unavailable
CALLBACK_PATH = "/settings/team/calendar-callback"

_transport: httpx2.AsyncBaseTransport | None = None


def set_transport(transport: httpx2.AsyncBaseTransport | None) -> None:
    """Route every Graph and login request through `transport` (tests)."""
    global _transport
    _transport = transport


class NotConfigured(RuntimeError):
    pass


class AuthRevoked(RuntimeError):
    """The user revoked access or the refresh token expired: reconnect needed."""


class GraphError(RuntimeError):
    def __init__(self, status: int, body: str) -> None:
        super().__init__(f"Graph {status}: {body[:300]}")
        self.status = status


def _client() -> httpx2.AsyncClient:
    return httpx2.AsyncClient(transport=_transport, timeout=20)


def configured(settings: Settings) -> bool:
    return bool(settings.ms_client_id and settings.ms_client_secret)


def redirect_uri(settings: Settings) -> str:
    return settings.dashboard_url.rstrip("/") + CALLBACK_PATH


# --- OAuth ---------------------------------------------------------------------------


@dataclass(frozen=True)
class OAuthState:
    tenant_id: UUID
    user_id: UUID
    resource_id: UUID
    verifier: str


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def authorize_url(settings: Settings, tenant_id: UUID, user_id: UUID, resource_id: UUID) -> str:
    """The Microsoft sign-in URL. The PKCE verifier travels inside the encrypted state,
    so the API keeps nothing between the redirect and the callback."""
    if not configured(settings):
        raise NotConfigured("CONFLUO_MS_CLIENT_ID / CONFLUO_MS_CLIENT_SECRET are not set")
    verifier = _b64(os.urandom(32))
    state = {
        "t": str(tenant_id),
        "u": str(user_id),
        "r": str(resource_id),
        "v": verifier,
        "exp": int(time.time()) + STATE_TTL,
    }
    sealed = _b64(seal(settings, json.dumps(state).encode(), aad=b"ms-oauth-state"))
    query = {
        "client_id": settings.ms_client_id,
        "response_type": "code",
        "redirect_uri": redirect_uri(settings),
        "response_mode": "query",
        "scope": SCOPES,
        "state": sealed,
        "code_challenge": _b64(hashlib.sha256(verifier.encode()).digest()),
        "code_challenge_method": "S256",
        "prompt": "select_account",
    }
    return f"{settings.ms_authority}/oauth2/v2.0/authorize?{urlencode(query)}"


def read_state(settings: Settings, state: str) -> OAuthState:
    try:
        data = json.loads(unseal(settings, _unb64(state), aad=b"ms-oauth-state"))
    except (InvalidTag, ValueError) as exc:
        raise ValueError("invalid state") from exc
    if data["exp"] < time.time():
        raise ValueError("expired state")
    return OAuthState(UUID(data["t"]), UUID(data["u"]), UUID(data["r"]), data["v"])


async def _token(settings: Settings, form: dict[str, str]) -> dict[str, Any]:
    assert settings.ms_client_id and settings.ms_client_secret
    form = {
        "client_id": settings.ms_client_id,
        "client_secret": settings.ms_client_secret.get_secret_value(),
        "scope": SCOPES,
        **form,
    }
    async with _client() as http:
        res = await http.post(f"{settings.ms_authority}/oauth2/v2.0/token", data=form)
    body = res.json()
    if res.status_code != 200:
        if body.get("error") in ("invalid_grant", "interaction_required", "consent_required"):
            raise AuthRevoked(body.get("error_description") or body["error"])
        raise GraphError(res.status_code, res.text)
    return {
        "access_token": body["access_token"],
        "refresh_token": body.get("refresh_token") or form.get("refresh_token"),
        "expires_at": int(time.time()) + int(body.get("expires_in", 3600)),
    }


async def exchange_code(settings: Settings, code: str, verifier: str) -> dict[str, Any]:
    return await _token(
        settings,
        {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": redirect_uri(settings),
            "code_verifier": verifier,
        },
    )


async def refresh(settings: Settings, refresh_token: str) -> dict[str, Any]:
    return await _token(settings, {"grant_type": "refresh_token", "refresh_token": refresh_token})


def needs_refresh(tokens: dict[str, Any]) -> bool:
    return int(tokens.get("expires_at", 0)) - 120 < time.time()


# --- Graph ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DeltaChange:
    event_id: str
    removed: bool
    start: datetime | None = None
    end: datetime | None = None
    blocking: bool = False


@dataclass(frozen=True)
class DeltaPage:
    changes: list[DeltaChange]
    delta_link: str


def _utc(value: dict[str, str]) -> datetime:
    # With `Prefer: outlook.timezone="UTC"` Graph returns e.g. 2026-10-05T09:00:00.0000000
    return datetime.fromisoformat(value["dateTime"][:19]).replace(tzinfo=UTC)


def _change(item: dict[str, Any]) -> DeltaChange:
    if "@removed" in item:
        return DeltaChange(item["id"], removed=True)
    blocking = item.get("showAs", "busy") in BLOCKING and not item.get("isCancelled", False)
    return DeltaChange(
        item["id"],
        removed=False,
        start=_utc(item["start"]),
        end=_utc(item["end"]),
        blocking=blocking,
    )


class GraphClient:
    def __init__(self, settings: Settings, access_token: str) -> None:
        self._base = settings.ms_graph_url.rstrip("/")
        self._headers = {
            "Authorization": f"Bearer {access_token}",
            "Prefer": 'outlook.timezone="UTC", odata.maxpagesize=200',
        }

    async def _call(self, method: str, url: str, **kw: Any) -> httpx2.Response:
        if not url.startswith("https://") and not url.startswith("http://"):
            url = self._base + url
        async with _client() as http:
            res = await http.request(method, url, headers=self._headers, **kw)
        if res.status_code == 401:
            raise AuthRevoked(res.text[:300])
        if res.status_code >= 400:
            raise GraphError(res.status_code, res.text)
        return res

    async def me(self) -> dict[str, Any]:
        data: dict[str, Any] = (await self._call("GET", "/me")).json()
        return data

    async def default_calendar(self) -> str:
        cal: str = (await self._call("GET", "/me/calendar")).json()["id"]
        return cal

    async def delta(
        self, calendar_id: str, *, delta_link: str | None, start: datetime, end: datetime
    ) -> DeltaPage:
        """All changes since `delta_link` (or every event in [start, end) without one),
        following nextLinks; returns them with the new deltaLink."""
        url = delta_link or (
            f"/me/calendars/{calendar_id}/calendarView/delta?"
            + urlencode(
                {
                    "startDateTime": start.astimezone(UTC).isoformat(),
                    "endDateTime": end.astimezone(UTC).isoformat(),
                }
            )
        )
        changes: list[DeltaChange] = []
        while True:
            body = (await self._call("GET", url)).json()
            changes += [_change(i) for i in body.get("value", [])]
            if "@odata.nextLink" in body:
                url = body["@odata.nextLink"]
                continue
            return DeltaPage(changes, body["@odata.deltaLink"])

    async def create_event(self, calendar_id: str, event: dict[str, Any]) -> str:
        res = await self._call("POST", f"/me/calendars/{calendar_id}/events", json=event)
        event_id: str = res.json()["id"]
        return event_id

    async def update_event(self, event_id: str, event: dict[str, Any]) -> None:
        await self._call("PATCH", f"/me/events/{event_id}", json=event)

    async def delete_event(self, event_id: str) -> None:
        try:
            await self._call("DELETE", f"/me/events/{event_id}")
        except GraphError as exc:
            if exc.status != 404:  # already gone
                raise

    async def subscribe(
        self, notification_url: str, client_state: str, expires: datetime
    ) -> tuple[str, datetime]:
        res = await self._call(
            "POST",
            "/subscriptions",
            json={
                "changeType": "created,updated,deleted",
                "notificationUrl": notification_url,
                "resource": "me/events",
                "expirationDateTime": expires.astimezone(UTC).isoformat(),
                "clientState": client_state,
            },
        )
        body = res.json()
        return body["id"], datetime.fromisoformat(body["expirationDateTime"])

    async def renew(self, subscription_id: str, expires: datetime) -> datetime:
        res = await self._call(
            "PATCH",
            f"/subscriptions/{subscription_id}",
            json={"expirationDateTime": expires.astimezone(UTC).isoformat()},
        )
        return datetime.fromisoformat(res.json()["expirationDateTime"])

    async def unsubscribe(self, subscription_id: str) -> None:
        try:
            await self._call("DELETE", f"/subscriptions/{subscription_id}")
        except GraphError as exc:
            if exc.status != 404:
                raise


def _graph_time(value: datetime) -> str:
    return value.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S")


def event_body(
    *, appointment_id: UUID, subject: str, text: str, start: datetime, end: datetime
) -> dict[str, Any]:
    return {
        "subject": subject,
        "body": {"contentType": "text", "content": text},
        "start": {
            "dateTime": start.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S"),
            "timeZone": "UTC",
        },
        "end": {"dateTime": end.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S"), "timeZone": "UTC"},
        "showAs": "busy",
        "categories": ["Confluo"],
        # Graph ignores a second POST with the same transactionId (job retries).
        "transactionId": str(appointment_id),
    }
