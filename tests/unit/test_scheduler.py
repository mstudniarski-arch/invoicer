from __future__ import annotations

from datetime import datetime
from unittest.mock import MagicMock

from invoicer.adapters.stub_approval import StubApprovalChannel
from invoicer.adapters.stub_detector import StubInvoiceDetector
from invoicer.models import InvoiceDocument
from invoicer.observability_status import PipelineCounters
from invoicer.scheduler import build_scheduler, run_daily_intake


class _FakeSource:
    def __init__(self, docs):
        self._docs = docs

    def fetch(self, sender):
        assert sender == "owner@example.com"
        return list(self._docs)


def _doc(name: str) -> InvoiceDocument:
    return InvoiceDocument(
        sender="owner@example.com",
        received_at=datetime(2026, 6, 24),
        filename=name,
        content=b"%PDF-1.4",
    )


def test_run_daily_intake_requests_approval_per_detected_invoice():
    docs = [_doc("a.pdf"), _doc("b.pdf")]
    channel = StubApprovalChannel()
    registry = MagicMock()
    graph = MagicMock()
    counters = PipelineCounters()
    # symulujemy bramke: payload = niepusty dict (wartosci pochodzace ze stanu)
    payload = {
        "seller": "ACME",
        "seller_nip": "5260001246",
        "number": "FV/1",
        "total_gross": "1230.00",
        "currency": "PLN",
        "treatment": "krajowa",
    }

    def fake_request(graph_, channel_, registry_, document, *, thread_id, phone):
        channel_.request_approval(payload)
        registry_.add(thread_id, phone)
        return payload

    run_daily_intake(
        graph,
        channel,
        registry,
        _FakeSource(docs),
        StubInvoiceDetector(result=True),
        sender="owner@example.com",
        phone="whatsapp:+48111",
        counters=counters,
        request_fn=fake_request,
    )
    assert len(channel.sent) == 2
    assert counters.processed == 2
    assert counters.failed == 0
    assert registry.add.call_count == 2


def test_run_daily_intake_skips_failed_invoice_and_continues():
    docs = [_doc("a.pdf"), _doc("b.pdf"), _doc("c.pdf")]
    channel = StubApprovalChannel()
    counters = PipelineCounters()
    calls = {"n": 0}

    def request_fn(graph, channel_, registry, document, *, thread_id, phone):
        calls["n"] += 1
        if document.filename == "b.pdf":
            raise RuntimeError("ekstrakcja padla")
        channel_.request_approval({"x": document.filename})
        return {"x": document.filename}

    run_daily_intake(
        MagicMock(),
        channel,
        MagicMock(),
        _FakeSource(docs),
        StubInvoiceDetector(result=True),
        sender="owner@example.com",
        phone="whatsapp:+48111",
        counters=counters,
        request_fn=request_fn,
    )
    assert calls["n"] == 3
    assert counters.processed == 2
    assert counters.failed == 1
    assert [m["x"] for m in channel.sent] == ["a.pdf", "c.pdf"]


def test_build_scheduler_adds_cron_job():
    sched = build_scheduler(lambda: None, hour=8, minute=0, tz="Europe/Warsaw")
    jobs = sched.get_jobs()
    assert len(jobs) == 1
    trigger = jobs[0].trigger
    assert getattr(trigger, "fields", None) is not None  # CronTrigger
    field_names = [f.name for f in trigger.fields]
    assert "hour" in field_names and "minute" in field_names
    assert str(trigger.timezone) == "Europe/Warsaw"
