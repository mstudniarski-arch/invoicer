from __future__ import annotations

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from langchain_core.callbacks import BaseCallbackHandler

_METRICS_LOGGER = "invoicer.metrics"

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


def _usage_from_response(response) -> tuple[int, int]:
    """Wyciaga (input_tokens, output_tokens) z LLMResult; brak danych -> (0, 0)."""
    try:
        message = response.generations[0][0].message
    except (AttributeError, IndexError):
        return 0, 0
    usage = getattr(message, "usage_metadata", None)
    if not usage:
        return 0, 0
    return int(usage.get("input_tokens", 0) or 0), int(usage.get("output_tokens", 0) or 0)


class LlmMetricsCallback(BaseCallbackHandler):
    """Mierzy latencje (per run_id) i koszt kazdego wywolania LLM.

    Zapis do kolektora + log (bez PII).
    """

    def __init__(
        self,
        metrics: LlmMetrics,
        *,
        model: str,
        clock: Callable[[], float] = time.monotonic,
        logger: logging.Logger | None = None,
    ) -> None:
        self._metrics = metrics
        self._model = model
        self._clock = clock
        self._logger = logger or logging.getLogger(_METRICS_LOGGER)
        # klucz run_id to uuid.UUID w produkcji (str w testach) — hash-based, oba dzialaja
        self._starts: dict[Any, float] = {}

    def on_chat_model_start(self, serialized, messages, *, run_id, **kwargs) -> None:
        self._starts[run_id] = self._clock()

    def on_llm_start(self, serialized, prompts, *, run_id, **kwargs) -> None:
        self._starts[run_id] = self._clock()

    def on_llm_error(self, error, *, run_id, **kwargs) -> None:
        # nieudane wywolanie nie wywola on_llm_end — sprzatamy start, by run_id nie wyciekal
        self._starts.pop(run_id, None)

    def on_llm_end(self, response, *, run_id, **kwargs) -> None:
        start = self._starts.pop(run_id, None)
        end = self._clock()
        latency_ms = round((end - start) * 1000) if start is not None else 0

        input_tokens, output_tokens = _usage_from_response(response)
        cost = estimate_cost(self._model, input_tokens, output_tokens)
        self._metrics.record(
            LlmCall(
                model=self._model,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                cost_usd=cost,
                latency_ms=latency_ms,
            )
        )
        self._logger.info(
            "llm_call model=%s input_tokens=%d output_tokens=%d cost_usd=%.6f latency_ms=%d",
            self._model,
            input_tokens,
            output_tokens,
            cost,
            latency_ms,
        )
