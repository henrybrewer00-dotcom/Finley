"""List prices in USD per million tokens (platform.claude.com pricing page, 2026-10).

Update these when Anthropic changes prices; every cost in the event log is computed from here.
"""

from __future__ import annotations

# model -> (input, output, cache_read)
PRICES: dict[str, tuple[float, float, float]] = {
    "claude-haiku-5-5": (0.10, 0.50, 0.01),
    "claude-sonnet-5-5": (2.00, 10.00, 0.10),
    "claude-opus-5-5": (4.00, 20.00, 0.20),
}
BATCH_DISCOUNT = 0.5
WEB_SEARCH_USD = 0.01  # $10 per 1,000 searches, on top of tokens


def cost_usd(model: str, input_tokens: int, output_tokens: int, cache_read_tokens: int = 0,
             batch: bool = False) -> float:
    inp, out, cached = PRICES.get(model, (0.0, 0.0, 0.0))
    total = (input_tokens * inp + output_tokens * out + cache_read_tokens * cached) / 1_000_000
    return total * BATCH_DISCOUNT if batch else total
