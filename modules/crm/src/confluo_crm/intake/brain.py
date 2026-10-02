"""Offline stand-in for the models, used by the `fake` LLM provider: deterministic
keyword rules for `intake_understand` and template replies for `intake_reply`.

It lets local development and CI run the whole intake pipeline without API keys
and gives the agent test harness a deterministic mode. It is not meant to be
clever; real conversations use Claude.
"""

import json
import re
from datetime import date, timedelta

from confluo_core.llm.fake_provider import register_responder
from confluo_core.llm.types import ChatRequest, ChatResult, Usage

WORDS: dict[str, list[str]] = {
    "human": [
        "human",
        "person",
        "someone",
        "medewerker",
        "iemand",
        "mens",
        "humain",
        "quelqu",
        "mitarbeiter",
        "mensch",
        "jemand",
        "njeri",
        "dikush",
    ],
    "cancel": [
        "cancel",
        "annuleer",
        "annuleren",
        "annuler",
        "absagen",
        "stornieren",
        "anulo",
        "anuloj",
    ],
    "reschedule": [
        "reschedule",
        "move my",
        "verplaats",
        "verzetten",
        "déplacer",
        "decaler",
        "verschieben",
        "zhvendos",
        "ndryshoj",
    ],
    "booking": [
        "book",
        "appointment",
        "afspraak",
        "boeken",
        "langskomen",
        "rendez",
        "réserver",
        "reserver",
        "termin",
        "buchen",
        "takim",
        "rezervo",
        "rezervim",
    ],
    "greeting": [
        "hi",
        "hello",
        "hey",
        "hallo",
        "hoi",
        "dag",
        "bonjour",
        "salut",
        "guten",
        "servus",
        "përshëndetje",
        "pershendetje",
        "tung",
        "thanks",
        "bedankt",
        "merci",
        "danke",
        "faleminderit",
    ],
    "faq": [
        "price",
        "cost",
        "open",
        "hours",
        "parking",
        "park",
        "where",
        "pay",
        "card",
        "prijs",
        "kost",
        "open",
        "uren",
        "parkeren",
        "waar",
        "betalen",
        "prix",
        "ouvert",
        "heures",
        "garer",
        "où",
        "payer",
        "preis",
        "kosten",
        "geöffnet",
        "öffnungszeiten",
        "parken",
        "wo",
        "bezahlen",
        "çmim",
        "cmim",
        "orari",
        "parkim",
        "ku",
        "paguaj",
    ],
}
STOPWORDS = {
    "nl": {
        "de",
        "het",
        "een",
        "ik",
        "je",
        "jullie",
        "kan",
        "is",
        "er",
        "wat",
        "niet",
        "met",
        "voor",
        "op",
    },
    "fr": {
        "le",
        "la",
        "les",
        "je",
        "vous",
        "est",
        "une",
        "un",
        "pour",
        "avec",
        "pas",
        "de",
        "du",
        "des",
    },
    "de": {
        "der",
        "die",
        "das",
        "ich",
        "sie",
        "ist",
        "ein",
        "eine",
        "nicht",
        "mit",
        "für",
        "und",
        "wir",
    },
    "sq": {
        "dhe",
        "një",
        "është",
        "unë",
        "ju",
        "për",
        "me",
        "nuk",
        "të",
        "në",
        "a",
        "ka",
        "keni",
        "përshëndetje",
        "faleminderit",
        "tung",
    },
    "en": {
        "the",
        "a",
        "an",
        "i",
        "you",
        "is",
        "are",
        "can",
        "do",
        "to",
        "for",
        "with",
        "my",
        "there",
    },
}


# fmt: off
# Greetings and thanks: short first messages often contain nothing else.
LANGUAGE_HINTS = {
    "nl": {"hoi", "bedankt", "graag", "dag", "goedemorgen"},
    "fr": {"bonjour", "merci", "salut", "beaucoup", "bonsoir"},
    "de": {"danke", "guten", "tag", "bitte", "morgen"},
    "sq": {"përshëndetje", "faleminderit", "tung", "mirëdita"},
    "en": {"hello", "thanks", "please", "morning"},
}
# fmt: on


def _language(text: str, browser: str | None = None) -> str:
    words = set(re.findall(r"\w+", text.lower()))
    scores = {lang: len(words & (stop | LANGUAGE_HINTS[lang])) for lang, stop in STOPWORDS.items()}
    best = max(scores, key=lambda lang: scores[lang])
    if scores[best] > 0:
        return best
    return browser if browser in STOPWORDS else "en"


def _intent(text: str) -> tuple[str, float]:
    low = text.lower()
    words = set(re.findall(r"\w+", low))
    for intent in ("human", "cancel", "reschedule", "booking"):
        if any((w in words) if " " not in w else (w in low) for w in WORDS[intent]):
            return intent, 0.9
    if "?" in text or words & set(WORDS["faq"]):
        return "faq", 0.8
    if words & set(WORDS["greeting"]):
        return "greeting", 0.9
    return "other", 0.4


def understand(request: ChatRequest) -> ChatResult:
    text = next((m.text for m in reversed(request.messages) if m.role == "user"), "")
    intent, confidence = _intent(text)
    browser = re.search(r"^BROWSER_LANGUAGE: (\w{2})", request.system, re.M)
    body = {
        # The whole conversation: short answers ("ja", "2") don't show a language.
        "language": _language(
            " ".join(m.text for m in request.messages if m.role == "user"),
            browser.group(1) if browser else None,
        ),
        "intent": intent,
        "confidence": confidence,
        "entities": {"service": None, "date": None, "time": None},
        "summary": f"keyword rules: {intent}",
    }
    return ChatResult(json.dumps(body), [], "end", request.model, Usage(len(text.split()) + 50, 40))


TEMPLATES = {
    "greet": {
        "en": "Hello! How can I help you?",
        "nl": "Hallo! Waarmee kan ik je helpen?",
        "fr": "Bonjour ! Comment puis-je vous aider ?",
        "de": "Hallo! Wie kann ich Ihnen helfen?",
        "sq": "Përshëndetje! Si mund t'ju ndihmoj?",
    },
    "handoff": {
        "en": "Thanks for your message! A colleague will take over and reply as soon as possible.",
        "nl": "Bedankt voor je bericht! Een collega neemt het over en antwoordt zo snel mogelijk.",
        "fr": "Merci pour votre message ! Un collègue prend le relais et vous répond au plus vite.",
        "de": "Danke für Ihre Nachricht! Ein Kollege übernimmt und antwortet so schnell wie möglich.",
        "sq": "Faleminderit për mesazhin! Një koleg do ta marrë përsipër dhe do t'ju përgjigjet sa më shpejt.",
    },
}


def reply(request: ChatRequest) -> ChatResult:
    sections = dict(re.findall(r"^(KIND|LANGUAGE): (.*)$", request.system, re.M))
    kind, lang = sections.get("KIND", "handoff"), sections.get("LANGUAGE", "en")
    knowledge = re.findall(
        r"^\[1\] (?:[^\n]*\n)?([^\[]+)", request.system.split("KNOWLEDGE:", 1)[-1], re.M
    )
    draft = re.search(r"^DRAFT: (.*)$", request.system, re.M)
    if kind == "answer" and knowledge:
        text = knowledge[0].strip()
    elif kind == "booking" and draft:
        text = draft.group(1).strip()
    else:
        text = TEMPLATES.get(kind, TEMPLATES["handoff"]).get(lang, TEMPLATES["handoff"]["en"])
    return ChatResult(
        text, [], "end", request.model, Usage(len(request.system.split()), len(text.split()))
    )


# --- Booking extraction (intake_booking) ----------------------------------------------

# fmt: off
TODAY_WORDS = {"today", "vandaag", "aujourd'hui", "aujourdhui", "heute", "sot"}
TOMORROW_WORDS = {"tomorrow", "morgen", "demain", "nesër", "neser"}
WEEKDAYS = [
    {"monday", "maandag", "lundi", "montag", "e hënë", "hene"},
    {"tuesday", "dinsdag", "mardi", "dienstag", "e martë", "marte"},
    {"wednesday", "woensdag", "mercredi", "mittwoch", "e mërkurë", "merkure"},
    {"thursday", "donderdag", "jeudi", "donnerstag", "e enjte", "enjte"},
    {"friday", "vrijdag", "vendredi", "freitag", "e premte", "premte"},
    {"saturday", "zaterdag", "samedi", "samstag", "e shtunë", "shtune"},
    {"sunday", "zondag", "dimanche", "sonntag", "e diel", "diel"},
]
YES = {"yes", "ja", "oui", "jawohl", "po", "ok", "okay", "graag", "prima", "sure", "yep", "d'accord", "goed"}
NO = {"no", "nee", "non", "nein", "jo", "neen"}
NAME_INTRO = r"(?:my name is|i am|i'm|this is|ik ben|mijn naam is|je m'appelle|je suis|ich bin|ich heiße|mein name ist|quhem|unë jam|jam)"
# fmt: on


def _section(system: str, name: str) -> list[str]:
    match = re.search(rf"^{name}:\n((?:(?:- |\d+\) ).*\n?)*)", system, re.M)
    return [line for line in (match.group(1).splitlines() if match else []) if line.strip()]


def booking(request: ChatRequest) -> ChatResult:
    text = next((m.text for m in reversed(request.messages) if m.role == "user"), "")
    low = text.lower().strip()
    words = set(re.findall(r"[\w']+", low))
    system = request.system
    today = date.fromisoformat(re.search(r"^TODAY: (\S+)", system, re.M).group(1))  # type: ignore[union-attr]
    stage = re.search(r"^STAGE: (\S+)", system, re.M).group(1)  # type: ignore[union-attr]
    asking = re.search(r"^ASKING: (\S+)", system, re.M).group(1)  # type: ignore[union-attr]
    services = [line[2:] for line in _section(system, "SERVICES")]
    options = [line for line in _section(system, "OPTIONS") if line[0].isdigit()]

    service = next((s for s in services if s.lower() in low), None) or next(
        (s for s in services if any(w in words for w in re.findall(r"\w{4,}", s.lower()))), None
    )
    day: date | None = None
    iso = re.search(r"\b(\d{4}-\d{2}-\d{2})\b", text)
    if iso:
        day = date.fromisoformat(iso.group(1))
    elif words & TODAY_WORDS or "aujourd'hui" in low:
        day = today
    elif words & TOMORROW_WORDS:
        day = today + timedelta(days=1)
    else:
        for i, names in enumerate(WEEKDAYS):
            if any(n in low for n in names):
                day = today + timedelta(days=(i - today.weekday()) % 7 or 7)
                break
    clock = re.search(r"\b(\d{1,2})[:.](\d{2})\b", text) or re.search(
        r"\b(\d{1,2})\s?(?:u|h|uur|uhr|am|pm|ora)\b", low
    )
    at = None
    if clock:
        hour = int(clock.group(1)) + (12 if "pm" in low and int(clock.group(1)) < 12 else 0)
        minute = int(clock.group(2)) if clock.lastindex and clock.lastindex > 1 else 0
        if hour < 24 and minute < 60:
            at = f"{hour:02d}:{minute:02d}"
    option = None
    picked = re.fullmatch(r"(?:option|optie|nummer|number|numéro|nr\.?)?\s*(\d)[.)!]?", low)
    if options and picked and 1 <= int(picked.group(1)) <= len(options):
        option, at = int(picked.group(1)), None

    name = None
    intro = re.search(NAME_INTRO + r"\s+([^\W\d][\w'-]+(?:\s+[A-Z][\w'-]+)?)", text, re.I)
    if intro:
        name = intro.group(1)
    elif asking == "name" and 0 < len(text.split()) <= 3 and not re.search(r"\d", text):
        name = text.strip().strip(".!")
    fields = []
    if stage == "details" and asking not in ("name", "-") and text.strip():
        fields.append({"key": asking, "value": text.strip()})
    confirm = None
    if stage == "confirm":
        confirm = "yes" if words & YES else "no" if words & NO else None

    body = {
        "service": service,
        "date": day.isoformat() if day else None,
        "time": at,
        "option": option,
        "name": name,
        "fields": fields,
        "confirm": confirm,
    }
    return ChatResult(json.dumps(body), [], "end", request.model, Usage(len(text.split()) + 80, 40))


def register() -> None:
    register_responder("intake_understand", understand)
    register_responder("intake_reply", reply)
    register_responder("intake_booking", booking)
