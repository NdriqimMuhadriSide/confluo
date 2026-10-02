"""Prompts of the intake graph. Kept short and factual; the sections with fixed
headings (KIND, KNOWLEDGE) are also what the offline stand-in brain reads."""

LANGUAGE_NAMES = {"en": "English", "nl": "Dutch", "fr": "French", "de": "German", "sq": "Albanian"}


def understand(business: str, browser_language: str | None = None) -> str:
    hint = (
        f"\nBROWSER_LANGUAGE: {browser_language} (use it only when the message itself "
        "doesn't show its language, e.g. a short greeting)"
        if browser_language
        else ""
    )
    return (
        f"You classify customer messages for {business}, a local business that takes "
        "appointments. Read the conversation and describe the customer's latest message.\n"
        "- language: the language the customer writes in (en, nl, fr, de or sq).\n"
        "- intent: booking (wants a new appointment), reschedule, cancel, faq (a question "
        "about the business: prices, hours, location, services, policies), greeting (only "
        "says hello or thanks), human (asks for a person), other (anything else).\n"
        "- confidence: 0 to 1, how sure you are about the intent.\n"
        "- entities: service, date and time if mentioned, as written by the customer.\n"
        "- summary: one short sentence in English." + hint
    )


FALLBACK = {
    "en": "Sorry, something went wrong on our side. A colleague will get back to you.",
    "nl": "Sorry, er ging iets mis bij ons. Een collega neemt contact met je op.",
    "fr": "Désolé, un problème est survenu de notre côté. Un collègue va vous recontacter.",
    "de": "Entschuldigung, bei uns ist etwas schiefgelaufen. Ein Kollege meldet sich bei Ihnen.",
    "sq": "Na vjen keq, diçka shkoi keq nga ana jonë. Një koleg do t'ju kontaktojë.",
}


def fallback(language: str) -> str:
    return FALLBACK.get(language, FALLBACK["en"])


def reply(
    *,
    business: str,
    language: str,
    kind: str,
    knowledge: list[dict[str, str]],
    handoff_reason: str | None,
    draft: str = "",
) -> str:
    lang = LANGUAGE_NAMES.get(language, "English")
    task = {
        "answer": (
            "Answer the customer's question using only the KNOWLEDGE below. If it doesn't "
            "contain the answer, say you'll ask a colleague. Never invent prices, times or "
            "policies."
        ),
        "greet": "Greet the customer back warmly and ask how you can help.",
        "handoff": (
            "Tell the customer a colleague will take over and reply as soon as possible. "
            "Don't promise a time, an appointment or an answer."
            + (" Start with the point of the DRAFT." if draft else "")
        ),
        "booking": (
            "Rewrite the DRAFT below as your reply. Keep every option number, day, date, "
            "time and name exactly as in the draft; add nothing else (no other times, "
            "prices or promises). Numbered options may go on separate lines."
        ),
    }[kind]
    lines = [
        f"You are the assistant of {business}. Reply to the customer in {lang}, in a "
        "friendly, short chat message (at most three sentences, no markdown).",
        f"KIND: {kind}",
        f"REASON: {handoff_reason or '-'}",
        f"LANGUAGE: {language}",
        f"TASK: {task}",
        f"DRAFT: {draft or '-'}",
        "KNOWLEDGE:",
    ]
    lines += [f"[{i + 1}] {k['content']}" for i, k in enumerate(knowledge)] or ["(none)"]
    return "\n".join(lines)
