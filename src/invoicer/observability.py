from __future__ import annotations

# Stawki USD za 1M tokenow. Zrodlo: referencja claude-api (cache 2026-06-04).
# Przyblizone i konfigurowalne — latwo zaktualizowac/rozszerzyc. To nie jest billing Anthropic.
_PRICING: dict[str, tuple[float, float]] = {
    # model: (input_usd_per_mtok, output_usd_per_mtok)
    "claude-sonnet-4-6": (3.0, 15.0),  # model domyslny adapterow
    "claude-opus-4-8": (5.0, 25.0),
    "claude-haiku-4-5": (1.0, 5.0),
}


def estimate_cost(model: str, input_tokens: int, output_tokens: int) -> float:
    """Szacowany koszt USD wywolania. Nieznany model -> 0.0 (tokeny/latencja i tak sa zapisane)."""
    rates = _PRICING.get(model)
    if rates is None:
        return 0.0
    in_rate, out_rate = rates
    return input_tokens / 1_000_000 * in_rate + output_tokens / 1_000_000 * out_rate
