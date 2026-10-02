# Demo: an AI receptionist for a hair salon

About 5 minutes. The story: a customer books on the salon's website at night; the
salon approves with one click in the morning; the customer is told in the chat.

## Before (once)

1. `make dev` (and `make seed` for a fresh demo salon: services, staff, hours, FAQ).
2. For natural conversations, set `ANTHROPIC_API_KEY` in `.env` and switch
   `CONFLUO_LLM_FAST` / `CONFLUO_LLM_DIALOGUE` from `fake:` to `anthropic:`; restart.
   Without a key the offline stand-in works too, but it needs exact phrases
   ("a women's cut", "tomorrow", "2", "my name is …", "yes").
3. Open two windows side by side:
   - the salon's website: http://127.0.0.1:3001/salon.html
   - the dashboard: http://localhost:3000 (demo@confluo.local / demo-confluo-2026) → **Calendar**

## The story

1. **Website → "Maak een afspraak".** Ask a question first: *"Can I park nearby?"*:
   the AI answers from the salon's knowledge base.
2. **Book:** *"I'd like a women's cut on Friday."* The AI offers real free times (it
   reads the staff schedules, days off and existing bookings), asks for the name and
   the details the salon requires (phone, hair length), sums up and books only after
   *"yes"*. Try it in Dutch or French: it answers in the customer's language.
3. **Dashboard → Calendar:** the request appears within seconds, marked *via AI*,
   waiting for approval. Click **Approve**.
4. **Back on the website:** the customer gets *"Good news: your appointment … is
   confirmed"* in the chat, live.
5. **Dashboard → Inbox:** the whole conversation; write a reply to take over from the
   AI, **Let the AI answer again** to hand it back.

Also worth showing: Settings → Modules → CRM → *Booking mode: Auto* (no approval
step), Settings → Team (working hours, days off), Knowledge base (what the AI knows).
