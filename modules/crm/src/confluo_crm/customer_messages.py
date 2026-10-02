"""Fixed messages to customers about their appointments, per language."""

# What the customer is told, in their language.
MESSAGES = {
    "confirmed": {
        "en": "Good news: your appointment ({service}, {when}) is confirmed. See you then!",
        "nl": "Goed nieuws: je afspraak ({service}, {when}) is bevestigd. Tot dan!",
        "fr": "Bonne nouvelle : votre rendez-vous ({service}, {when}) est confirmé. À bientôt !",
        "de": "Gute Nachricht: Ihr Termin ({service}, {when}) ist bestätigt. Bis dann!",
        "sq": "Lajm i mirë: takimi juaj ({service}, {when}) është konfirmuar. Shihemi!",
    },
    "rejected": {
        "en": "Sorry, we can't take your appointment ({service}, {when}). Would another time suit you?",
        "nl": "Sorry, we kunnen je afspraak ({service}, {when}) niet aannemen. Past een ander moment?",
        "fr": "Désolé, nous ne pouvons pas accepter votre rendez-vous ({service}, {when}). Un autre moment vous conviendrait-il ?",
        "de": "Leider können wir Ihren Termin ({service}, {when}) nicht annehmen. Passt Ihnen eine andere Zeit?",
        "sq": "Na vjen keq, nuk mund ta pranojmë takimin tuaj ({service}, {when}). A ju përshtatet një orë tjetër?",
    },
    "cancelled": {
        "en": "Your appointment ({service}, {when}) has been cancelled. Get in touch if you'd like a new one.",
        "nl": "Je afspraak ({service}, {when}) is geannuleerd. Laat het weten als je een nieuwe wilt.",
        "fr": "Votre rendez-vous ({service}, {when}) a été annulé. Contactez-nous pour en prendre un nouveau.",
        "de": "Ihr Termin ({service}, {when}) wurde abgesagt. Melden Sie sich gerne für einen neuen.",
        "sq": "Takimi juaj ({service}, {when}) u anulua. Na shkruani nëse dëshironi një të ri.",
    },
}
