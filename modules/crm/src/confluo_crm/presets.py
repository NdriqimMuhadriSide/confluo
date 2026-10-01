"""Industry presets: a starting configuration for a new business.

A preset sets the CRM config and creates booking fields, services (names in all
dashboard languages), starter staff/rooms with a weekly schedule, a location with
typical opening hours, and knowledge-base drafts in the business's language for the
owner to complete. Everything is ordinary data the owner can change afterwards.
"""

from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

from psycopg import AsyncConnection
from psycopg.types.json import Jsonb

from confluo_core.modules import Preset

LANGS = ("en", "nl", "fr", "de", "sq")
I18n = dict[str, str]


def t(en: str, nl: str, fr: str, de: str, sq: str) -> I18n:
    return {"en": en, "nl": nl, "fr": fr, "de": de, "sq": sq}


@dataclass(frozen=True)
class FieldSpec:
    entity: str
    key: str
    label: I18n
    type: str
    pii: str
    options: tuple[str, ...] = ()


@dataclass(frozen=True)
class ServiceSpec:
    name: I18n
    minutes: int
    price_eur: int
    fields: tuple[str, ...]  # field keys, all required
    buffer_after: int = 0


@dataclass(frozen=True)
class ResourceSpec:
    kind: str
    name: I18n


@dataclass(frozen=True)
class KbSpec:
    kind: str
    title: I18n
    body: I18n


@dataclass(frozen=True)
class PresetSpec:
    key: str
    name: I18n
    description: I18n
    config: dict[str, Any]
    hours: dict[str, list[tuple[str, str]]]
    fields: tuple[FieldSpec, ...]
    services: tuple[ServiceSpec, ...]
    resources: tuple[ResourceSpec, ...]
    kb: tuple[KbSpec, ...] = field(default=())


WEEKDAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
DAY_NAMES = {
    "en": ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"),
    "nl": ("maandag", "dinsdag", "woensdag", "donderdag", "vrijdag", "zaterdag", "zondag"),
    "fr": ("lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche"),
    "de": ("Montag", "Dienstag", "Mittwoch", "Donnerstag", "Freitag", "Samstag", "Sonntag"),
    "sq": ("e hënë", "e martë", "e mërkurë", "e enjte", "e premte", "e shtunë", "e diel"),
}
CLOSED = t("closed", "gesloten", "fermé", "geschlossen", "mbyllur")

PHONE = FieldSpec(
    "customer",
    "phone",
    t("Phone", "Telefoon", "Téléphone", "Telefon", "Telefoni"),
    "phone",
    "personal",
)

# Knowledge drafts every preset gets; [brackets] are for the owner to fill in.
COMMON_KB = (
    KbSpec(
        "location",
        t(
            "How to find us",
            "Waar vind je ons",
            "Comment nous trouver",
            "So finden Sie uns",
            "Si të na gjeni",
        ),
        t(
            "We are at [address]. [Parking and public transport].",
            "Je vindt ons op [adres]. [Parkeren en openbaar vervoer].",
            "Nous sommes situés [adresse]. [Parking et transports en commun].",
            "Sie finden uns in [Adresse]. [Parken und öffentliche Verkehrsmittel].",
            "Ndodhemi në [adresa]. [Parkimi dhe transporti publik].",
        ),
    ),
    KbSpec(
        "policy",
        t(
            "Cancelling or moving an appointment",
            "Een afspraak annuleren of verplaatsen",
            "Annuler ou déplacer un rendez-vous",
            "Termin absagen oder verschieben",
            "Anulimi ose zhvendosja e një takimi",
        ),
        t(
            "You can cancel or move your appointment free of charge up to [24] hours before. Later cancellations may be charged.",
            "Je kunt je afspraak gratis annuleren of verplaatsen tot [24] uur vooraf. Later annuleren kan aangerekend worden.",
            "Vous pouvez annuler ou déplacer votre rendez-vous gratuitement jusqu'à [24] heures avant. Une annulation tardive peut être facturée.",
            "Sie können Ihren Termin bis [24] Stunden vorher kostenlos absagen oder verschieben. Spätere Absagen können berechnet werden.",
            "Mund ta anuloni ose ta zhvendosni takimin falas deri [24] orë përpara. Anulimet e vonuara mund të faturohen.",
        ),
    ),
    KbSpec(
        "price",
        t("Payment", "Betalen", "Paiement", "Bezahlung", "Pagesa"),
        t(
            "You can pay by [Bancontact, card or cash].",
            "Je kunt betalen met [Bancontact, kaart of cash].",
            "Vous pouvez payer par [Bancontact, carte ou en espèces].",
            "Sie können mit [Bancontact, Karte oder bar] bezahlen.",
            "Mund të paguani me [Bancontact, kartë ose para në dorë].",
        ),
    ),
)


PRESETS: tuple[PresetSpec, ...] = (
    PresetSpec(
        key="salon",
        name=t(
            "Hair & beauty salon",
            "Kapsalon & schoonheidssalon",
            "Salon de coiffure & beauté",
            "Friseur- & Kosmetiksalon",
            "Sallon flokësh & bukurie",
        ),
        description=t(
            "Cuts, colour and styling, booked per stylist.",
            "Knippen, kleuren en brushen, per kapper geboekt.",
            "Coupes, couleurs et brushings, réservés par coiffeur.",
            "Schnitt, Farbe und Styling, pro Mitarbeiter gebucht.",
            "Prerje, ngjyrosje dhe stilim, të rezervuara sipas parukierit.",
        ),
        config={"booking_mode": "auto", "reminder_hours_before": 24, "ai_enabled": True},
        hours={d: [("09:00", "18:00")] for d in ("tue", "wed", "thu", "fri")}
        | {"sat": [("09:00", "16:00")]},
        fields=(
            PHONE,
            FieldSpec(
                "appointment",
                "hair_length",
                t(
                    "Hair length",
                    "Haarlengte",
                    "Longueur des cheveux",
                    "Haarlänge",
                    "Gjatësia e flokëve",
                ),
                "select",
                "none",
                ("short", "medium", "long"),
            ),
        ),
        services=(
            ServiceSpec(
                t(
                    "Women's cut",
                    "Knippen dames",
                    "Coupe femme",
                    "Damenhaarschnitt",
                    "Prerje për femra",
                ),
                45,
                42,
                ("phone", "hair_length"),
                10,
            ),
            ServiceSpec(
                t(
                    "Men's cut",
                    "Knippen heren",
                    "Coupe homme",
                    "Herrenhaarschnitt",
                    "Prerje për meshkuj",
                ),
                30,
                28,
                ("phone",),
                5,
            ),
            ServiceSpec(
                t("Colour", "Kleuren", "Coloration", "Färben", "Ngjyrosje"),
                90,
                65,
                ("phone", "hair_length"),
                10,
            ),
            ServiceSpec(
                t("Blow-dry", "Brushing", "Brushing", "Föhnen", "Fen"),
                30,
                25,
                ("phone", "hair_length"),
            ),
        ),
        resources=(
            ResourceSpec(
                "staff", t("Stylist 1", "Kapper 1", "Coiffeur 1", "Friseur 1", "Parukier 1")
            ),
        ),
        kb=COMMON_KB
        + (
            KbSpec(
                "faq",
                t(
                    "Do I need to wash my hair first?",
                    "Moet ik mijn haar vooraf wassen?",
                    "Dois-je me laver les cheveux avant ?",
                    "Muss ich meine Haare vorher waschen?",
                    "A duhet t'i laj flokët më parë?",
                ),
                t(
                    "No, a wash is included with every cut and colour.",
                    "Nee, wassen is inbegrepen bij elke knip- en kleurbeurt.",
                    "Non, le shampooing est compris dans chaque coupe et coloration.",
                    "Nein, Waschen ist bei jedem Schnitt und jeder Farbe inbegriffen.",
                    "Jo, larja përfshihet në çdo prerje dhe ngjyrosje.",
                ),
            ),
        ),
    ),
    PresetSpec(
        key="garage",
        name=t(
            "Car workshop / garage",
            "Garage / autowerkplaats",
            "Garage / atelier automobile",
            "Autowerkstatt",
            "Servis makinash",
        ),
        description=t(
            "Maintenance, tyres and inspection prep, with licence plate and car model.",
            "Onderhoud, banden en keuringsvoorbereiding, met nummerplaat en model.",
            "Entretien, pneus et préparation au contrôle technique, avec plaque et modèle.",
            "Wartung, Reifen und TÜV-Vorbereitung, mit Kennzeichen und Modell.",
            "Mirëmbajtje, goma dhe përgatitje për kontroll, me targë dhe model.",
        ),
        config={"booking_mode": "approval", "reminder_hours_before": 24, "ai_enabled": True},
        hours={
            d: [("08:00", "12:00"), ("13:00", "17:30")] for d in ("mon", "tue", "wed", "thu", "fri")
        },
        fields=(
            PHONE,
            FieldSpec(
                "appointment",
                "licence_plate",
                t(
                    "Licence plate",
                    "Nummerplaat",
                    "Plaque d'immatriculation",
                    "Kennzeichen",
                    "Targa",
                ),
                "text",
                "personal",
            ),
            FieldSpec(
                "appointment",
                "car_model",
                t(
                    "Car make and model",
                    "Merk en model",
                    "Marque et modèle",
                    "Marke und Modell",
                    "Marka dhe modeli",
                ),
                "text",
                "none",
            ),
            FieldSpec(
                "appointment",
                "mileage",
                t(
                    "Mileage (km)",
                    "Kilometerstand",
                    "Kilométrage",
                    "Kilometerstand",
                    "Kilometrazhi",
                ),
                "number",
                "none",
            ),
        ),
        services=(
            ServiceSpec(
                t("Oil change", "Olieverversing", "Vidange", "Ölwechsel", "Ndërrim vaji"),
                60,
                89,
                ("phone", "licence_plate", "car_model"),
                15,
            ),
            ServiceSpec(
                t(
                    "Tyre change",
                    "Bandenwissel",
                    "Changement de pneus",
                    "Reifenwechsel",
                    "Ndërrim gomash",
                ),
                45,
                60,
                ("phone", "licence_plate"),
                15,
            ),
            ServiceSpec(
                t(
                    "Inspection preparation",
                    "Keuringsvoorbereiding",
                    "Préparation au contrôle technique",
                    "TÜV-Vorbereitung",
                    "Përgatitje për kontroll teknik",
                ),
                90,
                120,
                ("phone", "licence_plate", "car_model", "mileage"),
                15,
            ),
            ServiceSpec(
                t("Diagnostic", "Diagnose", "Diagnostic", "Diagnose", "Diagnostikim"),
                30,
                45,
                ("phone", "licence_plate", "car_model"),
            ),
        ),
        resources=(
            ResourceSpec(
                "staff",
                t("Mechanic 1", "Mecanicien 1", "Mécanicien 1", "Mechaniker 1", "Mekanik 1"),
            ),
            ResourceSpec("room", t("Bay 1", "Brug 1", "Pont 1", "Hebebühne 1", "Ashensor 1")),
        ),
        kb=COMMON_KB
        + (
            KbSpec(
                "faq",
                t(
                    "Can I wait while you work?",
                    "Kan ik wachten tijdens de herstelling?",
                    "Puis-je attendre pendant l'intervention ?",
                    "Kann ich während der Arbeit warten?",
                    "A mund të pres gjatë punës?",
                ),
                t(
                    "Yes, for jobs under an hour you can wait in our waiting room. [Replacement car on request.]",
                    "Ja, voor werken van minder dan een uur kun je in onze wachtruimte wachten. [Vervangwagen op aanvraag.]",
                    "Oui, pour les interventions de moins d'une heure, vous pouvez attendre dans notre salle d'attente. [Véhicule de remplacement sur demande.]",
                    "Ja, bei Arbeiten unter einer Stunde können Sie in unserem Wartebereich warten. [Ersatzwagen auf Anfrage.]",
                    "Po, për punë nën një orë mund të prisni në sallën tonë të pritjes. [Makinë zëvendësuese me kërkesë.]",
                ),
            ),
        ),
    ),
    PresetSpec(
        key="physio",
        name=t(
            "Physio / clinic",
            "Kinesitherapie / praktijk",
            "Kinésithérapie / cabinet",
            "Physiotherapie / Praxis",
            "Fizioterapi / klinikë",
        ),
        description=t(
            "Treatments per therapist and room. Health details are marked sensitive and staff confirm each booking.",
            "Behandelingen per therapeut en ruimte. Gezondheidsgegevens zijn gemarkeerd als gevoelig en elke boeking wordt bevestigd.",
            "Soins par thérapeute et par salle. Les données de santé sont marquées sensibles et chaque réservation est confirmée.",
            "Behandlungen pro Therapeut und Raum. Gesundheitsdaten sind als sensibel markiert, jede Buchung wird bestätigt.",
            "Trajtime sipas terapistit dhe dhomës. Të dhënat shëndetësore shënohen si të ndjeshme dhe çdo rezervim konfirmohet.",
        ),
        # Health data (GDPR Art. 9): staff approve AI bookings.
        config={"booking_mode": "approval", "reminder_hours_before": 24, "ai_enabled": True},
        hours={d: [("08:00", "19:00")] for d in ("mon", "tue", "wed", "thu", "fri")},
        fields=(
            PHONE,
            FieldSpec(
                "customer",
                "date_of_birth",
                t(
                    "Date of birth",
                    "Geboortedatum",
                    "Date de naissance",
                    "Geburtsdatum",
                    "Data e lindjes",
                ),
                "date",
                "personal",
            ),
            FieldSpec(
                "appointment",
                "complaint",
                t(
                    "Reason for the visit",
                    "Reden van het bezoek",
                    "Motif de la visite",
                    "Grund des Besuchs",
                    "Arsyeja e vizitës",
                ),
                "long_text",
                "sensitive",
            ),
            FieldSpec(
                "appointment",
                "referral",
                t(
                    "Doctor's referral",
                    "Voorschrift van de arts",
                    "Prescription médicale",
                    "Ärztliche Verordnung",
                    "Udhëzim nga mjeku",
                ),
                "boolean",
                "sensitive",
            ),
        ),
        services=(
            ServiceSpec(
                t(
                    "First consultation",
                    "Eerste consultatie",
                    "Première consultation",
                    "Erstberatung",
                    "Konsulta e parë",
                ),
                45,
                60,
                ("phone", "date_of_birth", "complaint", "referral"),
                15,
            ),
            ServiceSpec(
                t(
                    "Follow-up session",
                    "Vervolgsessie",
                    "Séance de suivi",
                    "Folgebehandlung",
                    "Seancë pasuese",
                ),
                30,
                35,
                ("phone",),
                10,
            ),
            ServiceSpec(
                t(
                    "Sports massage",
                    "Sportmassage",
                    "Massage sportif",
                    "Sportmassage",
                    "Masazh sportiv",
                ),
                45,
                55,
                ("phone",),
                10,
            ),
            ServiceSpec(
                t(
                    "Home visit",
                    "Huisbezoek",
                    "Visite à domicile",
                    "Hausbesuch",
                    "Vizitë në shtëpi",
                ),
                60,
                70,
                ("phone", "complaint"),
            ),
        ),
        resources=(
            ResourceSpec(
                "staff",
                t("Therapist 1", "Therapeut 1", "Thérapeute 1", "Therapeut 1", "Terapist 1"),
            ),
            ResourceSpec(
                "room",
                t(
                    "Treatment room 1",
                    "Behandelruimte 1",
                    "Salle de soins 1",
                    "Behandlungsraum 1",
                    "Dhoma e trajtimit 1",
                ),
            ),
        ),
        kb=COMMON_KB
        + (
            KbSpec(
                "faq",
                t(
                    "Is treatment reimbursed?",
                    "Wordt de behandeling terugbetaald?",
                    "Le traitement est-il remboursé ?",
                    "Wird die Behandlung erstattet?",
                    "A rimbursohet trajtimi?",
                ),
                t(
                    "With a doctor's referral, part of the cost is reimbursed by your health insurance. [Bring your referral to the first session.]",
                    "Met een voorschrift van de arts betaalt je ziekenfonds een deel terug. [Breng het voorschrift mee naar de eerste sessie.]",
                    "Avec une prescription médicale, votre mutuelle rembourse une partie des frais. [Apportez la prescription à la première séance.]",
                    "Mit ärztlicher Verordnung erstattet Ihre Krankenkasse einen Teil der Kosten. [Bringen Sie die Verordnung zur ersten Sitzung mit.]",
                    "Me udhëzim nga mjeku, sigurimi shëndetësor rimburson një pjesë të kostos. [Sillni udhëzimin në seancën e parë.]",
                ),
            ),
        ),
    ),
    PresetSpec(
        key="generic",
        name=t(
            "Generic appointments",
            "Algemene afspraken",
            "Rendez-vous génériques",
            "Allgemeine Termine",
            "Takime të përgjithshme",
        ),
        description=t(
            "A bare start for consultants, coaches and other businesses.",
            "Een eenvoudige start voor consultants, coaches en andere zaken.",
            "Un point de départ simple pour consultants, coachs et autres.",
            "Ein schlichter Start für Berater, Coaches und andere Betriebe.",
            "Një fillim i thjeshtë për konsulentë, trajnerë dhe biznese të tjera.",
        ),
        config={"booking_mode": "approval", "reminder_hours_before": 24, "ai_enabled": True},
        hours={d: [("09:00", "17:00")] for d in ("mon", "tue", "wed", "thu", "fri")},
        fields=(PHONE,),
        services=(
            ServiceSpec(
                t("Consultation", "Consultatie", "Consultation", "Beratung", "Konsultë"),
                30,
                0,
                ("phone",),
            ),
            ServiceSpec(
                t("Appointment", "Afspraak", "Rendez-vous", "Termin", "Takim"), 60, 0, ("phone",)
            ),
        ),
        resources=(
            ResourceSpec(
                "staff",
                t(
                    "Team member 1",
                    "Teamlid 1",
                    "Membre de l'équipe 1",
                    "Teammitglied 1",
                    "Anëtar i ekipit 1",
                ),
            ),
        ),
        kb=COMMON_KB,
    ),
)

BY_KEY = {p.key: p for p in PRESETS}
CATALOG = [Preset(p.key, p.name, p.description) for p in PRESETS]


def hours_text(hours: dict[str, list[tuple[str, str]]], lang: str) -> str:
    names = DAY_NAMES[lang]
    lines = []
    for i, day in enumerate(WEEKDAYS):
        spans = hours.get(day, [])
        text = ", ".join(f"{a}–{b}" for a, b in spans) if spans else CLOSED[lang]
        lines.append(f"{names[i].capitalize()}: {text}")
    return "\n".join(lines)


HOURS_TITLE = t(
    "Opening hours", "Openingsuren", "Heures d'ouverture", "Öffnungszeiten", "Orari i hapjes"
)
LOCATION_NAME = t(
    "Main location",
    "Hoofdvestiging",
    "Établissement principal",
    "Hauptstandort",
    "Vendndodhja kryesore",
)


async def apply_preset(conn: AsyncConnection, key: str, lang: str, timezone: str) -> None:
    """Create the preset's data in the transaction's tenant (app.tenant_id set)."""
    spec = BY_KEY[key]
    lang = lang if lang in LANGS else "en"

    await conn.execute(
        "insert into tenant_module (module_key, enabled, config) values ('crm', true, %s)"
        " on conflict (tenant_id, module_key) do update set config = excluded.config",
        (Jsonb(spec.config),),
    )
    hours_json = {d: [{"start": a, "end": b} for a, b in spec.hours.get(d, [])] for d in WEEKDAYS}
    cur = await conn.execute(
        "insert into location (name, timezone, opening_hours) values (%s, %s, %s) returning id",
        (LOCATION_NAME[lang], timezone, Jsonb(hours_json)),
    )
    row = await cur.fetchone()
    assert row is not None
    location_id: UUID = row[0]

    field_ids: dict[str, UUID] = {}
    for pos, f in enumerate(spec.fields):
        cur = await conn.execute(
            "insert into field_definition (entity, key, label_i18n, type, options, pii_level, position)"
            " values (%s, %s, %s, %s, %s, %s, %s) returning id",
            (f.entity, f.key, Jsonb(f.label), f.type, Jsonb(list(f.options)), f.pii, pos),
        )
        row = await cur.fetchone()
        assert row is not None
        field_ids[f.key] = row[0]

    staff_ids: list[UUID] = []
    for r in spec.resources:
        cur = await conn.execute(
            "insert into crm_resource (kind, name, location_id) values (%s, %s, %s) returning id",
            (r.kind, r.name[lang], location_id),
        )
        row = await cur.fetchone()
        assert row is not None
        if r.kind == "staff":
            staff_ids.append(row[0])
        # Starter schedule = the location's opening hours.
        for i, day in enumerate(WEEKDAYS):
            for start, end in spec.hours.get(day, []):
                await conn.execute(
                    "insert into crm_availability_rule (resource_id, weekday, start_time, end_time)"
                    " values (%s, %s, %s, %s)",
                    (row[0], i + 1, start, end),
                )

    for pos, s in enumerate(spec.services):
        cur = await conn.execute(
            "insert into crm_service (name_i18n, duration_min, buffer_after_min, price_cents, position)"
            " values (%s, %s, %s, %s, %s) returning id",
            (Jsonb(s.name), s.minutes, s.buffer_after, s.price_eur * 100 or None, pos),
        )
        row = await cur.fetchone()
        assert row is not None
        for fpos, fkey in enumerate(s.fields):
            await conn.execute(
                "insert into crm_service_field (service_id, field_definition_id, required, position)"
                " values (%s, %s, true, %s)",
                (row[0], field_ids[fkey], fpos),
            )
        for rid in staff_ids:
            await conn.execute(
                "insert into crm_service_resource (service_id, resource_id) values (%s, %s)",
                (row[0], rid),
            )

    # Knowledge drafts in the business's language; the owner reviews and publishes.
    drafts = [("hours", HOURS_TITLE[lang], hours_text(spec.hours, lang))]
    drafts += [(k.kind, k.title[lang], k.body[lang]) for k in spec.kb]
    for kind, title, body in drafts:
        await conn.execute(
            "insert into crm_knowledge_item (kind, title, body, language) values (%s, %s, %s, %s)",
            (kind, title, body, lang),
        )
