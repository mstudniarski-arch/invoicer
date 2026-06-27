from __future__ import annotations

import logging

from invoicer.app import _METRICS_MODEL, _real_claude_adapters
from invoicer.ledger import Ledger, LedgerEntry
from invoicer.observability import LlmMetrics, LlmMetricsCallback
from invoicer.observability_status import PipelineCounters, pipeline_status
from invoicer.runner import _run_config, active_sink_name
from invoicer.security import RedactingFilter, install_redaction


class _Registry:
    def count_pending(self, *, phone=None):
        return 0


def test_active_sink_name_default_and_fakturownia(monkeypatch):
    monkeypatch.delenv("INVOICER_SINK", raising=False)
    assert active_sink_name() == "mock-subiekt"
    monkeypatch.setenv("INVOICER_SINK", "fakturownia")
    assert active_sink_name() == "fakturownia"


def test_pipeline_status_includes_sink():
    out = pipeline_status(LlmMetrics(), PipelineCounters(), _Registry(), sink="fakturownia")
    assert out["sink"] == "fakturownia"
    assert "llm" in out and "pipeline" in out


def test_ledger_records_provenance_and_chain_still_verifies(tmp_path):
    ledger = Ledger(tmp_path / "l.jsonl")
    ledger.append(
        LedgerEntry(
            number="FV/1",
            seller_name="ACME",
            total_gross="1230.00",
            booking_id="FK-9",
            booked_at="2026-01-01T10:00:00",
            sink="fakturownia",
            treatment="krajowa",
            thread_id="intake-abc",
        )
    )
    entry = ledger.entries()[0]
    assert (entry.sink, entry.treatment, entry.thread_id) == (
        "fakturownia",
        "krajowa",
        "intake-abc",
    )
    assert ledger.verify_chain() is True


def test_old_entry_without_provenance_still_verifies(tmp_path):
    # Wpis "po staremu" (bez sink/treatment/thread_id) musi nadal przechodzic verify_chain,
    # bo nowe pola sa POZA hashem rdzenia finansowego.
    ledger = Ledger(tmp_path / "l.jsonl")
    ledger.append(
        LedgerEntry(
            number="FV/2",
            seller_name="X",
            total_gross="100.00",
            booking_id="MOCK-FV/2",
            booked_at="2026-01-01T10:00:00",
        )
    )
    assert ledger.verify_chain() is True


def test_real_claude_adapters_get_metrics_callback():
    # Krytyczny fix: prod FAKTYCZNIE podpina LlmMetricsCallback (wczesniej /status zawsze $0).
    metrics = LlmMetrics()
    extractor, reasoner = _real_claude_adapters(metrics)
    assert any(isinstance(cb, LlmMetricsCallback) for cb in extractor._callbacks)
    assert any(isinstance(cb, LlmMetricsCallback) for cb in reasoner._callbacks)
    assert _METRICS_MODEL == "claude-sonnet-4-6"


def test_run_config_tags_thread_id():
    cfg = _run_config("intake-xyz")
    assert cfg["configurable"]["thread_id"] == "intake-xyz"
    assert cfg["run_name"] == "invoice-intake-xyz"
    assert cfg["metadata"]["thread_id"] == "intake-xyz"
    assert "invoicer" in cfg["tags"]


def test_install_redaction_defaults_to_root():
    install_redaction()  # brak argumentu -> root logger
    root = logging.getLogger()
    assert any(any(isinstance(flt, RedactingFilter) for flt in h.filters) for h in root.handlers)
