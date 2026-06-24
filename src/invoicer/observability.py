from __future__ import annotations

from dataclasses import dataclass

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


@dataclass(frozen=True)
class LlmCall:
    model: str
    input_tokens: int
    output_tokens: int
    cost_usd: float
    latency_ms: int


class LlmMetrics:
    """Kolektor in-memory wywolan LLM."""

    def __init__(self) -> None:
        self.calls: list[LlmCall] = []

    def record(self, call: LlmCall) -> None:
        self.calls.append(call)

    def totals(self) -> dict[str, int | float]:
        input_tokens = sum(c.input_tokens for c in self.calls)
        output_tokens = sum(c.output_tokens for c in self.calls)
        return {
            "n_calls": len(self.calls),
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "total_tokens": input_tokens + output_tokens,
            "cost_usd": sum(c.cost_usd for c in self.calls),
            "latency_ms": sum(c.latency_ms for c in self.calls),
        }
