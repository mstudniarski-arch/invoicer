from __future__ import annotations

from datetime import datetime, timedelta
from unittest.mock import MagicMock

import pytest

from invoicer.adapters.stub_approval import StubApprovalChannel
from invoicer.adapters.stub_detector import StubInvoiceDetector
from invoicer.models import InvoiceDocument
from invoicer.observability_status import PipelineCounters
from invoicer.processed import document_key
from invoicer.scheduler import build_scheduler, run_intake


class _FakeSource:
    def __init__(self, docs):
        self._docs = docs

    def fetch(self, sender):
        assert sender == "owner@example.com"
        return list(self._docs)


class _FakeProcessed:
    """In-memory namiastka ProcessedDocuments do testow jednostkowych."""

    def __init__(self, seen=()):
        self._seen = set(seen)
        self.marks: list[tuple[str, str]] = []

    def seen(self, key):
        return key in self._seen

    def mark(self, key, status):
        self.marks.append((key, status))
        self._seen.add(key)


def _doc(name: str) -> InvoiceDocument:
    return InvoiceDocument(
        sender="owner@example.com",
        received_at=datetime(2026, 6, 24),
        filename=name,
        content=b"%PDF-1.4",
    )


def test_run_intake_requests_approval_per_detected_invoice():
    docs = [_doc("a.pdf"), _doc("b.pdf")]
    channel = StubApprovalChannel()
    registry = MagicMock()
    counters = PipelineCounters()
    processed = _FakeProcessed()
    payload = {"number": "FV/1", "total_gross": "1230.00", "currency": "PLN"}

    def fake_request(graph_, channel_, registry_, document, *, thread_id, phone):
        channel_.request_approval(payload)
        registry_.add(thread_id, phone)
        return payload

    run_intake(
        MagicMock(), channel, registry, _FakeSource(docs),
        StubInvoiceDetector(result=True),
        sender="owner@example.com", phone="whatsapp:+48111",
        counters=counters, processed=processed, request_fn=fake_request,
    )
    assert len(channel.sent) == 2
    assert counters.processed == 2
    assert counters.failed == 0
    assert registry.add.call_count == 2
    assert processed.marks == [
        (document_key(docs[0]), "done"),
        (document_key(docs[1]), "done"),
    ]


def test_run_intake_skips_already_processed():
    docs = [_doc("a.pdf"), _doc("b.pdf")]
    processed = _FakeProcessed(seen={document_key(docs[0])})  # a.pdf juz obsluzony
    channel = StubApprovalChannel()
    counters = PipelineCounters()
    calls: list[str] = []

    def request_fn(graph, channel_, registry, document, *, thread_id, phone):
        calls.append(document.filename)
        channel_.request_approval({"x": document.filename})
        return {"x": document.filename}

    run_intake(
        MagicMock(), channel, MagicMock(), _FakeSource(docs),
        StubInvoiceDetector(result=True),
        sender="owner@example.com", phone="whatsapp:+48111",
        counters=counters, processed=processed, request_fn=request_fn,
    )
    assert calls == ["b.pdf"]  # a.pdf pominiety (idempotencja)
    assert counters.processed == 1
    assert processed.marks == [(document_key(docs[1]), "done")]


def test_run_intake_marks_failed_and_alerts_without_retry():
    docs = [_doc("a.pdf"), _doc("b.pdf"), _doc("c.pdf")]
    channel = StubApprovalChannel()
    counters = PipelineCounters()
    processed = _FakeProcessed()
    alerts: list[tuple[str, str]] = []

    def request_fn(graph, channel_, registry, document, *, thread_id, phone):
        if document.filename == "b.pdf":
            raise RuntimeError("ekstrakcja padla")
        channel_.request_approval({"x": document.filename})
        return {"x": document.filename}

    run_intake(
        MagicMock(), channel, MagicMock(), _FakeSource(docs),
        StubInvoiceDetector(result=True),
        sender="owner@example.com", phone="whatsapp:+48111",
        counters=counters, processed=processed, request_fn=request_fn,
        alert=lambda ctx, reason: alerts.append((ctx, reason)),
    )
    assert [m["x"] for m in channel.sent] == ["a.pdf", "c.pdf"]
    assert counters.processed == 2
    assert counters.failed == 1
    # b.pdf zapisany jako 'failed' (NIE bedzie ponowiony) i zaalarmowany raz
    assert (document_key(docs[1]), "failed") in processed.marks
    assert len(alerts) == 1
    assert alerts[0][0] == "b.pdf"
    assert "manualnej interwencji" in alerts[0][1]
    assert "ekstrakcja padla" in alerts[0][1]


class _BoomSource:
    def fetch(self, sender):
        raise RuntimeError("token Gmaila wygasl")


def test_run_intake_alerts_when_fetch_fails():
    # blad zaciagu (poza petla per-faktura) MUSI zaalarmowac i podniesc wyjatek
    alerts: list[tuple[str, str]] = []
    with pytest.raises(RuntimeError, match="token Gmaila wygasl"):
        run_intake(
            MagicMock(), StubApprovalChannel(), MagicMock(), _BoomSource(),
            StubInvoiceDetector(result=True),
            sender="owner@example.com", phone="whatsapp:+48111",
            counters=PipelineCounters(), processed=_FakeProcessed(),
            alert=lambda ctx, reason: alerts.append((ctx, reason)),
        )
    assert alerts == [("intake", "token Gmaila wygasl")]


def test_build_scheduler_adds_interval_job():
    sched = build_scheduler(lambda: None, interval_minutes=5)
    jobs = sched.get_jobs()
    assert len(jobs) == 1
    job = jobs[0]
    assert job.id == "intake"
    assert job.max_instances == 1
    assert job.coalesce is True
    assert job.trigger.interval == timedelta(minutes=5)  # IntervalTrigger, nie cron
