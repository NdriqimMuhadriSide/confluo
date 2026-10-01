"""Create (or recreate) the demo tenant `demo`: a hair salon in Ghent with realistic data.

    make seed            # local stack; also creates the login demo@confluo.local

Built on the `salon` industry preset (so the preset code runs), then filled like a
salon a few weeks after go-live: three stylists with schedules and a holiday, eight
customers with contact details and consent, past and upcoming appointments, three
conversations on different channels, and the knowledge base completed, published and
queued for indexing.

Runs as the database owner. Idempotent: deletes the existing `demo` tenant first.
If SUPABASE_LOCAL_SECRET_KEY is set (make seed passes it), a Supabase user
demo@confluo.local / demo-confluo-2026 is created and made owner.
"""

import asyncio
import json
import os
import urllib.error
import urllib.request
import uuid
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import psycopg
from psycopg import AsyncConnection
from psycopg.types.json import Jsonb

from confluo_core.settings import get_settings
from confluo_crm.presets import apply_preset

SLUG = "demo"
DEMO_EMAIL = "demo@confluo.local"
DEMO_PASSWORD = "demo-confluo-2026"  # local demo only
TZ = ZoneInfo("Europe/Brussels")


def demo_user_id(supabase_url: str) -> uuid.UUID | None:
    """Create or find the demo Supabase user; None when no secret key is available."""
    key = os.environ.get("SUPABASE_LOCAL_SECRET_KEY")
    if not key:
        return None
    headers = {"apikey": key, "Authorization": f"Bearer {key}", "Content-Type": "application/json"}
    body = json.dumps({"email": DEMO_EMAIL, "password": DEMO_PASSWORD, "email_confirm": True})
    req = urllib.request.Request(
        f"{supabase_url}/auth/v1/admin/users", data=body.encode(), headers=headers, method="POST"
    )
    try:
        with urllib.request.urlopen(req) as res:
            return uuid.UUID(json.load(res)["id"])
    except urllib.error.HTTPError as e:
        if e.code != 422:
            raise
    req = urllib.request.Request(
        f"{supabase_url}/auth/v1/admin/users?per_page=1000", headers=headers
    )
    with urllib.request.urlopen(req) as res:
        users = json.load(res)["users"]
    return next(uuid.UUID(u["id"]) for u in users if u["email"] == DEMO_EMAIL)


def at(day: date, hh: int, mm: int = 0) -> datetime:
    return datetime.combine(day, time(hh, mm), TZ)


async def one(conn: AsyncConnection, sql: str, *args: object) -> uuid.UUID:
    cur = await conn.execute(sql, args)
    row = await cur.fetchone()
    assert row is not None
    return uuid.UUID(str(row[0]))


async def seed(
    conn: AsyncConnection, owner: uuid.UUID | None, today: date | None = None
) -> uuid.UUID:
    today = today or date.today()
    await conn.execute("delete from tenant where slug = %s", (SLUG,))
    tenant = await one(
        conn,
        "insert into tenant (name, slug, default_locale, timezone)"
        " values ('Kapsalon Demo', %s, 'nl', 'Europe/Brussels') returning id",
        SLUG,
    )
    # Owner connection: RLS doesn't apply, but column defaults use the tenant setting.
    await conn.execute("select set_config('app.tenant_id', %s, true)", (str(tenant),))
    if owner:
        await conn.execute(
            "insert into app_user (id, email, name) values (%s, %s, 'Demo Eigenaar') on conflict (id) do nothing",
            (owner, DEMO_EMAIL),
        )
        await conn.execute(
            "insert into tenant_member (tenant_id, user_id, role) values (%s, %s, 'owner')",
            (tenant, owner),
        )

    await apply_preset(conn, "salon", "nl", "Europe/Brussels")

    location = await one(conn, "select id from location where tenant_id = %s", tenant)
    await conn.execute(
        "update location set name = 'Gent centrum', address = 'Veldstraat 12, 9000 Gent' where id = %s",
        (location,),
    )

    # The preset's "Kapper 1" becomes Eva; Lotte and Sam join with their own days.
    staff = {
        "Eva": await one(
            conn, "select id from crm_resource where tenant_id = %s and kind = 'staff'", tenant
        )
    }
    await conn.execute("update crm_resource set name = 'Eva' where id = %s", (staff["Eva"],))
    for name, days in (("Lotte", (2, 3, 4, 5, 6)), ("Sam", (3, 5, 6))):
        rid = await one(
            conn,
            "insert into crm_resource (kind, name, location_id) values ('staff', %s, %s) returning id",
            name,
            location,
        )
        staff[name] = rid
        for wd in days:
            end = time(16) if wd == 6 else time(18)
            await conn.execute(
                "insert into crm_availability_rule (resource_id, weekday, start_time, end_time) values (%s, %s, '09:00', %s)",
                (rid, wd, end),
            )
        await conn.execute(
            "insert into crm_service_resource (service_id, resource_id) select id, %s from crm_service where tenant_id = %s",
            (rid, tenant),
        )
    next_week = today + timedelta(days=7 - today.weekday())
    await conn.execute(
        "insert into crm_availability_exception (resource_id, during, kind, reason)"
        " values (%s, tstzrange(%s, %s), 'off', 'Verlof')",
        (
            staff["Lotte"],
            at(next_week + timedelta(days=1), 0),
            at(next_week + timedelta(days=3), 0),
        ),
    )

    cur = await conn.execute(
        "select name_i18n->>'en', id from crm_service where tenant_id = %s", (tenant,)
    )
    services = {name: sid for name, sid in await cur.fetchall()}

    customers: dict[str, uuid.UUID] = {}
    for display, lang, phone, email in [
        ("Sofie Peeters", "nl", "+32470123456", "sofie.peeters@example.be"),
        ("Lucas Dubois", "fr", "+32471234567", "lucas.dubois@example.be"),
        ("Emma Janssens", "nl", "+32472345678", None),
        ("Noah Maes", "en", "+32473456789", "noah@example.com"),
        ("Liam Jacobs", "nl", "+32474567890", None),
        ("Chloé Lambert", "fr", "+32475678901", "chloe.lambert@example.be"),
        ("Arben Krasniqi", "sq", "+32476789012", "arben.k@example.com"),
        ("Hannah Schmitz", "de", "+32477890123", "hannah.schmitz@example.de"),
    ]:
        first, last = display.split(" ", 1)
        cid = await one(
            conn,
            "insert into customer (display_name, first_name, last_name, preferred_language, custom_fields)"
            " values (%s, %s, %s, %s, %s) returning id",
            display,
            first,
            last,
            lang,
            Jsonb({"phone": phone}),
        )
        customers[display] = cid
        identities = [("phone", phone, "phone"), ("whatsapp", phone, "whatsapp")]
        if email:
            identities.append(("email", email, "email"))
        for kind, value, channel in identities:
            await conn.execute(
                "insert into customer_identity (customer_id, type, value_normalized, verified, source_channel)"
                " values (%s, %s, %s, true, %s)",
                (cid, kind, value, channel),
            )
        await conn.execute(
            "insert into consent (customer_id, purpose, granted, channel, text_version) values (%s, 'processing', true, 'web', 'v1')",
            (cid,),
        )

    minutes = {"Women's cut": 45, "Men's cut": 30, "Colour": 90, "Blow-dry": 30}
    for customer, service, person, offset, hh, mm, status, source in [
        ("Sofie Peeters", "Blow-dry", "Eva", -14, 10, 0, "completed", "staff"),
        ("Liam Jacobs", "Men's cut", "Sam", -9, 15, 0, "completed", "ai"),
        ("Emma Janssens", "Colour", "Eva", -7, 13, 0, "no_show", "online"),
        ("Sofie Peeters", "Women's cut", "Eva", 1, 10, 0, "confirmed", "ai"),
        ("Lucas Dubois", "Men's cut", "Sam", 2, 11, 0, "confirmed", "staff"),
        ("Emma Janssens", "Colour", "Lotte", 3, 13, 0, "confirmed", "ai"),
        ("Noah Maes", "Men's cut", "Eva", 4, 9, 30, "pending_approval", "ai"),
        ("Chloé Lambert", "Women's cut", "Lotte", 8, 14, 0, "confirmed", "online"),
        ("Arben Krasniqi", "Men's cut", "Sam", 9, 16, 0, "confirmed", "ai"),
        ("Hannah Schmitz", "Blow-dry", "Eva", 10, 10, 30, "confirmed", "staff"),
    ]:
        day = today + timedelta(days=offset)
        if day.isoweekday() in (1, 7):  # salon closed on Sunday and Monday
            day += timedelta(days=1 if day.isoweekday() == 1 else 2)
        start = at(day, hh, mm)
        await conn.execute(
            "insert into crm_appointment (customer_id, service_id, resource_id, during, status, source, field_values)"
            " values (%s, %s, %s, tstzrange(%s, %s), %s, %s, %s)",
            (
                customers[customer],
                services[service],
                staff[person],
                start,
                start + timedelta(minutes=minutes[service]),
                status,
                source,
                Jsonb({"hair_length": "medium"} if service != "Men's cut" else {}),
            ),
        )

    # Three conversations, as the inbox will show them.
    for customer, channel, language, handler, msgs in [
        (
            "Sofie Peeters",
            "whatsapp",
            "nl",
            "ai",
            [
                ("inbound", "customer", "Hoi! Kan ik vrijdag om 10u komen knippen?"),
                (
                    "outbound",
                    "ai",
                    "Hallo Sofie! Vrijdag om 10:00 kan bij Eva. Zal ik het vastleggen?",
                ),
                ("inbound", "customer", "Ja graag!"),
                (
                    "outbound",
                    "ai",
                    "Top, je afspraak voor knippen dames staat vast op vrijdag om 10:00 bij Eva. Tot dan!",
                ),
            ],
        ),
        (
            "Lucas Dubois",
            "web",
            "fr",
            "ai",
            [
                ("inbound", "customer", "Bonjour, vous acceptez les cartes ?"),
                (
                    "outbound",
                    "ai",
                    "Bonjour ! Oui, vous pouvez payer par Bancontact, Visa, Mastercard ou en espèces.",
                ),
            ],
        ),
        (
            "Noah Maes",
            "email",
            "en",
            "human",
            [
                (
                    "inbound",
                    "customer",
                    "Hi, I'd like a men's cut next week but I can only come after 17:00.",
                ),
                (
                    "outbound",
                    "ai",
                    "Thanks Noah! I've asked the team whether a late slot is possible; they'll get back to you today.",
                ),
            ],
        ),
    ]:
        conv = await one(
            conn,
            "insert into crm_conversation (customer_id, channel, status, handler, language, last_message_at)"
            " values (%s, %s, 'open', %s, %s, now()) returning id",
            customers[customer],
            channel,
            handler,
            language,
        )
        for i, (direction, sender, body) in enumerate(msgs):
            await conn.execute(
                "insert into crm_message (conversation_id, direction, sender_type, body, delivery_status, created_at)"
                " values (%s, %s, %s, %s, %s, now() - make_interval(mins => %s))",
                (
                    conv,
                    direction,
                    sender,
                    body,
                    "delivered" if direction == "outbound" else None,
                    (len(msgs) - i) * 3,
                ),
            )

    # Complete the preset's knowledge drafts and publish them.
    fill = {
        "[adres]": "Veldstraat 12 in Gent, vlak bij de Korenmarkt",
        "[Parkeren en openbaar vervoer]": "Betaald parkeren in parking Vrijdagmarkt (3 minuten). Tram 1 stopt voor de deur",
        "[24]": "24",
        "[Bancontact, kaart of cash]": "Bancontact, Visa, Mastercard of cash",
    }
    cur = await conn.execute(
        "select id, body from crm_knowledge_item where tenant_id = %s", (tenant,)
    )
    for kid, body in await cur.fetchall():
        for placeholder, value in fill.items():
            body = body.replace(placeholder, value)
        await conn.execute(
            "update crm_knowledge_item set body = %s, published = true, published_at = now() where id = %s",
            (body, kid),
        )
    for title, body, lang in [
        (
            "Parking",
            "Paid parking at the Vrijdagmarkt car park, a 3-minute walk. Tram 1 stops right outside.",
            "en",
        ),
        (
            "Horaires",
            "Du mardi au vendredi de 9h à 18h, le samedi de 9h à 16h. Fermé le dimanche et le lundi.",
            "fr",
        ),
    ]:
        await conn.execute(
            "insert into crm_knowledge_item (kind, title, body, language, published, published_at)"
            " values ('faq', %s, %s, %s, true, now())",
            (title, body, lang),
        )
    return tenant


async def queue_indexing(tenant: uuid.UUID) -> int:
    """Index the demo's knowledge base through the normal job, so search works."""
    from confluo_core.db import create_pool
    from confluo_core.job_app import build_job_app
    from confluo_core.modules import discover_modules

    settings = get_settings()
    with psycopg.connect(str(settings.migrations_database_url)) as conn:
        items = [
            r[0]
            for r in conn.execute(
                "select id from crm_knowledge_item where tenant_id = %s and published", (tenant,)
            ).fetchall()
        ]
    app = build_job_app(discover_modules())
    pool = create_pool(settings)
    await pool.open()
    try:
        async with app.open_async(pool):
            for item in items:
                await app.tasks["crm:embed_knowledge_item"].defer_async(
                    tenant_id=str(tenant), item_id=str(item)
                )
    finally:
        await pool.close()
    return len(items)


async def main() -> None:
    settings = get_settings()
    owner = demo_user_id(settings.supabase_url)
    url = str(settings.migrations_database_url)
    async with await AsyncConnection.connect(url) as conn, conn.transaction():
        tenant = await seed(conn, owner)
    queued = await queue_indexing(tenant)
    print(f"demo tenant {tenant} (slug '{SLUG}'); {queued} knowledge items queued for indexing")
    if owner:
        print(f"sign in as {DEMO_EMAIL} / {DEMO_PASSWORD}")
    else:
        print("no SUPABASE_LOCAL_SECRET_KEY: tenant created without a login (run via `make seed`)")


if __name__ == "__main__":
    asyncio.run(main())
