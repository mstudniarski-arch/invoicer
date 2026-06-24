from dataclasses import dataclass

from invoicer.observability import LlmCall, LlmMetrics
from invoicer.observability_status import PipelineCounters, pipeline_status


@dataclass
class _FakeRegistry:
    pending: int

    def count_pending(self, *, phone: str | None = None) -> int:
        return self.pending


def test_pipeline_status_combines_llm_totals_counters_and_pending():
    metrics = LlmMetrics()
    metrics.record(LlmCall("claude-sonnet-4-6", 100, 20, 0.0006, 500))
    counters = PipelineCounters(processed=3, failed=1)
    registry = _FakeRegistry(pending=2)

    st = pipeline_status(metrics, counters, registry, phone="whatsapp:+48111")

    assert st["llm"]["n_calls"] == 1
    assert st["llm"]["input_tokens"] == 100
    assert st["pipeline"]["processed"] == 3
    assert st["pipeline"]["failed"] == 1
    assert st["pipeline"]["pending"] == 2


def test_pipeline_status_phone_filter_passed_to_registry():
    captured = {}

    class _Reg:
        def count_pending(self, *, phone=None):
            captured["phone"] = phone
            return 0

    pipeline_status(LlmMetrics(), PipelineCounters(), _Reg(), phone="whatsapp:+48111")
    assert captured["phone"] == "whatsapp:+48111"


def test_counters_default_zero():
    c = PipelineCounters()
    assert c.processed == 0 and c.failed == 0
    c.incr_processed()
    c.incr_failed()
    c.incr_failed()
    assert c.processed == 1 and c.failed == 2
