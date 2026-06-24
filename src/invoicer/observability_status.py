from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from invoicer.observability import LlmMetrics


class _Registry(Protocol):
    def count_pending(self, *, phone: str | None = None) -> int: ...


@dataclass
class PipelineCounters:
    """In-memory liczniki pipeline'u (reset przy restarcie — biezacy podglad)."""

    processed: int = 0
    failed: int = 0

    def incr_processed(self) -> None:
        self.processed += 1

    def incr_failed(self) -> None:
        self.failed += 1


def pipeline_status(
    metrics: LlmMetrics,
    counters: PipelineCounters,
    registry: _Registry,
    *,
    phone: str | None = None,
) -> dict:
    """Agreguje stan dla GET /status: koszt/latencja LLM + liczniki + pending."""
    return {
        "llm": metrics.totals(),
        "pipeline": {
            "processed": counters.processed,
            "failed": counters.failed,
            "pending": registry.count_pending(phone=phone),
        },
    }
