"""Create (or recreate) the demo tenant `demo` with realistic salon data.

    make seed            # local stack; also creates the login demo@confluo.local

Runs as the database owner. Idempotent: deletes the existing `demo` tenant first.
If SUPABASE_LOCAL_SECRET_KEY is set (make seed passes it), a Supabase user
demo@confluo.local / demo-confluo-2026 is created and made owner.
"""

import json
import os
import urllib.error
import urllib.request
import uuid
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import psycopg
from psycopg.types.json import Jsonb

from confluo_core.settings import get_settings

SLUG = "demo"
DEMO_EMAIL = "demo@confluo.local"
DEMO_PASSWORD = "demo-confluo-2026"  # local demo only
TZ = ZoneInfo("Europe/Brussels")


def demo_user_id(settings_url: str) -> uuid.UUID | None:
    """Create or find the demo Supabase user; None when no secret key is available."""
    key = os.environ.get("SUPABASE_LOCAL_SECRET_KEY")
    if not key:
        return None
    headers = {"apikey": key, "Authorization": f"Bearer {key}", "Content-Type": "application/json"}
    body = json.dumps({"email": DEMO_EMAIL, "password": DEMO_PASSWORD, "email_confirm": True})
    req = urllib.request.Request(
        f"{settings_url}/auth/v1/admin/users", data=body.encode(), headers=headers, method="POST"
    )
    try:
        with urllib.request.urlopen(req) as res:
            return uuid.UUID(json.load(res)["id"])
    except urllib.error.HTTPError as e:
        if e.code != 422:
            raise
    # Already exists: look it up.
    req = urllib.request.Request(
        f"{settings_url}/auth/v1/admin/users?per_page=1000", headers=headers
    )
    with urllib.request.urlopen(req) as res:
        users = json.load(res)["users"]
    return next(uuid.UUID(u["id"]) for u in users if u["email"] == DEMO_EMAIL)


def at(day: date, hh: int, mm: int = 0) -> datetime:
    return datetime.combine(day, time(hh, mm), TZ)


def seed(conn: psycopg.Connection, owner: uuid.UUID | None) -> uuid.UUID:
    conn.execute("delete from tenant where slug = %s", (SLUG,))
    row = conn.execute(
        "insert into tenant (name, slug, default_locale, timezone)"
        " values ('Kapsalon Demo', %s, 'nl', 'Europe/Brussels') returning id",
        (SLUG,),
    ).fetchone()
    assert row is not None
    tenant = uuid.UUID(str(row[0]))

    def ins(sql: str, *args: object) -> uuid.UUID:
        row = conn.execute(sql, (tenant, *args)).fetchone()
        assert row is not None
        return uuid.UUID(str(row[0]))

    if owner:
        conn.execute(
            "insert into app_user (id, email, name) values (%s, %s, 'Demo Owner')"
            " on conflict (id) do nothing",
            (owner, DEMO_EMAIL),
        )
        conn.execute(
            "insert into tenant_member (tenant_id, user_id, role) values (%s, %s, 'owner')",
            (tenant, owner),
        )

    hours = {d: [["09:00", "18:00"]] for d in ("mon", "tue", "wed", "thu", "fri")} | {
        "sat": [["09:00", "16:00"]]
    }
    location = ins(
        "insert into location (tenant_id, name, address, timezone, opening_hours)"
        " values (%s, 'Gent centrum', 'Veldstraat 12, 9000 Gent', 'Europe/Brussels', %s)"
        " returning id",
        Jsonb(hours),
    )

    hair_length = ins(
        "insert into field_definition (tenant_id, entity, key, label_i18n, type, options, pii_level)"
        " values (%s, 'appointment', 'hair_length', %s, 'select', %s, 'none') returning id",
        Jsonb({"en": "Hair length", "nl": "Haarlengte", "fr": "Longueur des cheveux"}),
        Jsonb(["short", "medium", "long"]),
    )
    phone_field = ins(
        "insert into field_definition (tenant_id, entity, key, label_i18n, type, pii_level)"
        " values (%s, 'customer', 'phone', %s, 'phone', 'personal') returning id",
        Jsonb({"en": "Phone", "nl": "Telefoon", "fr": "Téléphone"}),
    )

    services: dict[str, uuid.UUID] = {}
    for name, minutes, price, needs_length in [
        ({"en": "Women's cut", "nl": "Knippen dames", "fr": "Coupe femme"}, 45, 4200, True),
        ({"en": "Men's cut", "nl": "Knippen heren", "fr": "Coupe homme"}, 30, 2800, False),
        ({"en": "Colour", "nl": "Kleuren", "fr": "Coloration"}, 90, 6500, True),
        ({"en": "Blow-dry", "nl": "Brushing", "fr": "Brushing"}, 30, 2500, True),
    ]:
        sid = ins(
            "insert into crm_service (tenant_id, name_i18n, duration_min, buffer_after_min, price_cents)"
            " values (%s, %s, %s, 10, %s) returning id",
            Jsonb(name),
            minutes,
            price,
        )
        services[name["en"]] = sid
        conn.execute(
            "insert into crm_service_field (tenant_id, service_id, field_definition_id, required)"
            " values (%s, %s, %s, true)",
            (tenant, sid, phone_field),
        )
        if needs_length:
            conn.execute(
                "insert into crm_service_field (tenant_id, service_id, field_definition_id, required, position)"
                " values (%s, %s, %s, true, 1)",
                (tenant, sid, hair_length),
            )

    staff: dict[str, uuid.UUID] = {}
    for person, days in [
        ("Eva", (1, 2, 3, 4, 5)),
        ("Lotte", (2, 3, 4, 5, 6)),
        ("Sam", (1, 3, 5, 6)),
    ]:
        rid = ins(
            "insert into crm_resource (tenant_id, kind, location_id, name)"
            " values (%s, 'staff', %s, %s) returning id",
            location,
            person,
        )
        staff[person] = rid
        for wd in days:
            end = time(16) if wd == 6 else time(18)
            conn.execute(
                "insert into crm_availability_rule (tenant_id, resource_id, weekday, start_time, end_time)"
                " values (%s, %s, %s, '09:00', %s)",
                (tenant, rid, wd, end),
            )
        for sid in services.values():
            conn.execute(
                "insert into crm_service_resource (tenant_id, service_id, resource_id)"
                " values (%s, %s, %s)",
                (tenant, sid, rid),
            )

    today = date.today()
    next_monday = today + timedelta(days=(7 - today.weekday()) % 7 or 7)
    conn.execute(
        "insert into crm_availability_exception (tenant_id, resource_id, during, kind, reason)"
        " values (%s, %s, tstzrange(%s, %s), 'off', 'Holiday')",
        (
            tenant,
            staff["Lotte"],
            at(next_monday + timedelta(days=1), 0),
            at(next_monday + timedelta(days=3), 0),
        ),
    )

    customers: dict[str, uuid.UUID] = {}
    for display, first, last, lang, phone, email in [
        ("Sofie Peeters", "Sofie", "Peeters", "nl", "+32470123456", "sofie.peeters@example.be"),
        ("Lucas Dubois", "Lucas", "Dubois", "fr", "+32471234567", "lucas.dubois@example.be"),
        ("Emma Janssens", "Emma", "Janssens", "nl", "+32472345678", None),
        ("Noah Maes", "Noah", "Maes", "en", "+32473456789", "noah@example.com"),
        ("Liam Jacobs", "Liam", "Jacobs", "nl", "+32474567890", None),
    ]:
        cid = ins(
            "insert into customer (tenant_id, display_name, first_name, last_name, preferred_language)"
            " values (%s, %s, %s, %s, %s) returning id",
            display,
            first,
            last,
            lang,
        )
        customers[display] = cid
        conn.execute(
            "insert into customer_identity (tenant_id, customer_id, type, value_normalized, verified, source_channel)"
            " values (%s, %s, 'phone', %s, true, 'phone')",
            (tenant, cid, phone),
        )
        if email:
            conn.execute(
                "insert into customer_identity (tenant_id, customer_id, type, value_normalized, verified, source_channel)"
                " values (%s, %s, 'email', %s, true, 'email')",
                (tenant, cid, email),
            )
        conn.execute(
            "insert into consent (tenant_id, customer_id, purpose, granted, channel, text_version)"
            " values (%s, %s, 'processing', true, 'web', 'v1')",
            (tenant, cid),
        )

    for customer, service, person, day_offset, hh, mm, status in [
        ("Sofie Peeters", "Women's cut", "Eva", 0, 10, 0, "confirmed"),
        ("Lucas Dubois", "Men's cut", "Sam", 0, 11, 0, "confirmed"),
        ("Emma Janssens", "Colour", "Eva", 2, 13, 0, "confirmed"),
        ("Noah Maes", "Men's cut", "Lotte", 3, 9, 30, "pending_approval"),
        ("Liam Jacobs", "Blow-dry", "Sam", 4, 15, 0, "confirmed"),
        ("Sofie Peeters", "Blow-dry", "Eva", -7, 10, 0, "completed"),
    ]:
        day = next_monday + timedelta(days=day_offset)
        minutes = {"Women's cut": 45, "Men's cut": 30, "Colour": 90, "Blow-dry": 30}[service]
        start = at(day, hh, mm)
        conn.execute(
            "insert into crm_appointment (tenant_id, customer_id, service_id, resource_id, during, status, source, field_values)"
            " values (%s, %s, %s, %s, tstzrange(%s, %s), %s, 'staff', %s)",
            (
                tenant,
                customers[customer],
                services[service],
                staff[person],
                start,
                start + timedelta(minutes=minutes),
                status,
                Jsonb({"hair_length": "medium"} if service != "Men's cut" else {}),
            ),
        )

    for kind, title, body, lang in [
        (
            "hours",
            "Openingsuren",
            "Maandag tot vrijdag 9u–18u, zaterdag 9u–16u. Zondag gesloten.",
            "nl",
        ),
        (
            "location",
            "Waar vind je ons",
            "Veldstraat 12 in Gent, vlak bij de Korenmarkt. Tram 1 stopt voor de deur.",
            "nl",
        ),
        ("faq", "Parking", "Betaald parkeren in parking Vrijdagmarkt, 3 minuten wandelen.", "nl"),
        (
            "policy",
            "Annuleren",
            "Annuleren kan gratis tot 24 uur vooraf. Later annuleren kost 50% van de prijs.",
            "nl",
        ),
        ("faq", "Payment", "We accept Bancontact, Visa, Mastercard and cash.", "en"),
        ("hours", "Horaires", "Du lundi au vendredi de 9h à 18h, le samedi de 9h à 16h.", "fr"),
    ]:
        conn.execute(
            "insert into crm_knowledge_item (tenant_id, kind, title, body, language, published, published_at)"
            " values (%s, %s, %s, %s, %s, true, now())",
            (tenant, kind, title, body, lang),
        )
    return tenant


def main() -> None:
    settings = get_settings()
    owner = demo_user_id(settings.supabase_url)
    with psycopg.connect(str(settings.migrations_database_url)) as conn, conn.transaction():
        tenant = seed(conn, owner)
    print(f"demo tenant {tenant} (slug '{SLUG}')")
    if owner:
        print(f"sign in as {DEMO_EMAIL} / {DEMO_PASSWORD}")
    else:
        print("no SUPABASE_LOCAL_SECRET_KEY: tenant created without a login (run via `make seed`)")


if __name__ == "__main__":
    main()
