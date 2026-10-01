# External calendars

Each staff member (or room) can connect one external calendar. Confluo then:

- **writes confirmed bookings** to it as events (moved, reassigned or cancelled
  bookings follow; pending ones stay out until confirmed);
- **reads busy time** from it into `crm_external_busy`, which availability subtracts:
  a dentist appointment in Outlook blocks that hour for online booking.

Microsoft 365 / Outlook works today; Google Calendar will plug into the same tables
and jobs.

## How it works

| Piece | Where |
|---|---|
| Sign-in (OAuth 2.0 code flow + PKCE, encrypted `state`) | `confluo_crm/calendar/microsoft.py`, `calendar/api.py` |
| Tokens, encrypted with AES-256-GCM (`CONFLUO_SECRETS_KEY`) | core `secret` table, `confluo_core/secrets.py` |
| Busy time: Graph `calendarView/delta` (incremental, delta link in `sync_token`) | job `crm:sync_calendar` |
| Bookings → events: a trigger on `crm_appointment` queues a job on every change | job `crm:push_appointment`, migration `crm_0005` |
| Push: Graph change notifications → `/webhooks/microsoft-calendar` → sync | `calendar/webhook.py` |
| Safety net: every active connection synced every 5 minutes | periodic job `crm:sync_all_calendars` |

Details worth knowing:

- Busy, tentative and out-of-office events block time; free events, cancelled ones and
  Confluo's own events don't.
- The delta window covers yesterday to 180 days ahead and restarts (a full read)
  when fewer than 30 days remain, or when Microsoft expires the delta token.
- Revoked access (password change, consent withdrawn) marks the connection `revoked`;
  the dashboard asks to connect again.
- Change notifications need the API reachable over public HTTPS
  (`CONFLUO_PUBLIC_API_URL`). Locally they're off and the 5-minute sync covers it.
- Disconnecting deletes the tokens, the busy times and the subscription; events
  already in Outlook stay there.

## Setting up the Microsoft app (once per environment)

1. [Azure portal](https://portal.azure.com) → **Microsoft Entra ID → App registrations →
   New registration**.
   - Name: `Confluo` (customers see it on the consent screen).
   - Supported account types: **Accounts in any organizational directory and personal
     Microsoft accounts** (salons use both Microsoft 365 and outlook.com).
   - Redirect URI: platform **Web**, `http://localhost:3000/settings/team/calendar-callback`
     (add the production dashboard's `/settings/team/calendar-callback` later).
2. **Certificates & secrets → New client secret**; copy the *value*.
3. **API permissions → Add → Microsoft Graph → Delegated**: `Calendars.ReadWrite`,
   `User.Read`, `offline_access`.
4. In `.env`:

   ```
   CONFLUO_MS_CLIENT_ID=<Application (client) ID from the Overview page>
   CONFLUO_MS_CLIENT_SECRET=<the secret value>
   CONFLUO_SECRETS_KEY=<uv run python -c "from confluo_core.secrets import new_key; print(new_key())">
   ```

5. Restart `make dev`, open **Settings → Team → (a person) → Connect Outlook**.

Client secrets expire (at most 24 months): put a reminder in the calendar.
