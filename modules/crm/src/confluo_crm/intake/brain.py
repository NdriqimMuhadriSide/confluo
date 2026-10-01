"""Offline stand-in for the models, used by the `fake` LLM provider: deterministic
keyword rules for `intake_understand` and template replies for `intake_reply`.

It lets local development and CI run the whole intake pipeline without API keys
and gives the agent test harness a deterministic mode. It is not meant to be
clever; real conversations use Claude.
"""

import json
import re

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
        "language": _language(text, browser.group(1) if browser else None),
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
    if kind == "answer" and knowledge:
        text = knowledge[0].strip()
    else:
        text = TEMPLATES.get(kind, TEMPLATES["handoff"]).get(lang, TEMPLATES["handoff"]["en"])
    return ChatResult(
        text, [], "end", request.model, Usage(len(request.system.split()), len(text.split()))
    )


def register() -> None:
    register_responder("intake_understand", understand)
    register_responder("intake_reply", reply)
