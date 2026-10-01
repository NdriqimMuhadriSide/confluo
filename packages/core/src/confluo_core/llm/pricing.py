"""Estimated USD prices per million tokens, for the per-tenant cost view.

Estimates only (list prices as of 2026-10; invoices are the source of truth).
Cache reads cost 0.1x and cache writes 1.25x of the input price on Claude.
Unknown models cost 0 and are flagged in the usage view.
"""

from decimal import Decimal

from confluo_core.llm.types import Usage

# model -> (input, output) USD per 1M tokens
PRICES: dict[str, tuple[Decimal, Decimal]] = {
    "claude-opus-5": (Decimal("5"), Decimal("25")),
    "claude-opus-4-8": (Decimal("5"), Decimal("25")),
    "claude-sonnet-5": (Decimal("2"), Decimal("10")),
    "claude-haiku-4-5": (Decimal("1"), Decimal("5")),
    "voyage-3.5": (Decimal("0.06"), Decimal("0")),
    "voyage-3.5-lite": (Decimal("0.02"), Decimal("0")),
}
MILLION = Decimal(1_000_000)


def estimate_cost(model: str, usage: Usage) -> Decimal | None:
    price = PRICES.get(model)
    if price is None:
        return None
    inp, out = price
    total = (
        inp * usage.input_tokens
        + inp * Decimal("0.1") * usage.cache_read_tokens
        + inp * Decimal("1.25") * usage.cache_write_tokens
        + out * usage.output_tokens
    )
    return (total / MILLION).quantize(Decimal("0.000001"))
