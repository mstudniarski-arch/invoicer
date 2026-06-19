from datetime import date, datetime
from decimal import Decimal

from langgraph.types import Command

from invoicer.adapters.mock_subiekt import MockSubiektSink
from invoicer.adapters.stub_extractor import StubExtractor
from invoicer.adapters.stub_reasoner import StubExceptionReasoner
from invoicer.graph.build import build_invoice_graph
from invoicer.ledger import Ledger
from invoicer.models import (
    Classification,
    CountryBucket,
    Invoice,
    InvoiceDocument,
    LineItem,
    Party,
    TaxTreatment,
)


def _invoice() -> Invoice:
    line = LineItem(
        description="Usluga",
        quantity=Decimal("1"),
        unit_net=Decimal("1000.00"),
        vat_rate=Decimal("0.23"),
        net=Decimal("1000.00"),
        vat=Decimal("230.00"),
        gross=Decimal("1230.00"),
    )
    return Invoice(
        seller=Party(name="ACME", nip="5260001246", country="PL"),
        buyer=Party(name="Klient", country="PL"),
        number="FV/1",
        issue_date=date(2026, 6, 1),
        currency="PLN",
        lines=[line],
        total_net=Decimal("1000.00"),
        total_vat=Decimal("230.00"),
        total_gross=Decimal("1230.00"),
        extraction_confidence=0.95,
    )


def _doc() -> InvoiceDocument:
    return InvoiceDocument(
        sender="a@b.pl", received_at=datetime(2026, 6, 1), filename="x.pdf", content=b"%PDF"
    )


def _graph(ledger):
    return build_invoice_graph(
        extractor=StubExtractor(_invoice()),
        ledger=ledger,
        sink=MockSubiektSink(),
        clock=lambda: "2026-06-01T10:00:00",
    )


def test_graph_pauses_at_human_review_then_books_on_approve(tmp_path):
    graph = _graph(Ledger(tmp_path / "l.jsonl"))
    config = {"configurable": {"thread_id": "t1"}}
    paused = graph.invoke({"document": _doc(), "errors": []}, config)
    # Graf zatrzymal sie na human_review -> jeszcze nie zaksiegowano.
    assert paused.get("booking") is None
    final = graph.invoke(Command(resume="approve"), config)
    assert final["booking"].booking_id == "MOCK-FV/1"


def test_graph_does_not_book_on_reject(tmp_path):
    ledger = Ledger(tmp_path / "l.jsonl")
    graph = _graph(ledger)
    config = {"configurable": {"thread_id": "t2"}}
    graph.invoke({"document": _doc(), "errors": []}, config)
    final = graph.invoke(Command(resume="reject"), config)
    assert final.get("booking") is None
    assert ledger.entries() == []


def _foreign_invoice() -> Invoice:
    inv = _invoice()
    inv.seller = Party(name="Foreign Ltd", country="GB", vat_id="GB1")
    inv.total_vat = Decimal("0.00")
    inv.total_gross = Decimal("1000.00")
    inv.currency = "GBP"
    inv.lines[0].vat = Decimal("0.00")
    inv.lines[0].vat_rate = Decimal("0.00")
    inv.lines[0].gross = Decimal("1000.00")
    return inv


def test_foreign_invoice_runs_through_reason_exception(tmp_path):
    enriched = Classification(
        treatment=TaxTreatment.IMPORT_TOWAROW,
        country_bucket=CountryBucket.POZA_UE,
        confidence=0.9,
        rationale_pl="towar wg sedziego",
    )
    graph = build_invoice_graph(
        extractor=StubExtractor(_foreign_invoice()),
        ledger=Ledger(tmp_path / "l.jsonl"),
        sink=MockSubiektSink(),
        reasoner=StubExceptionReasoner(enriched),
        clock=lambda: "2026-06-01T10:00:00",
    )
    config = {"configurable": {"thread_id": "f1"}}
    paused = graph.invoke({"document": _doc(), "errors": []}, config)
    # po reason_exception klasyfikacja jest wzbogacona przez sedziego
    assert paused["classification"].treatment == TaxTreatment.IMPORT_TOWAROW
    assert paused["classification"].rationale_pl == "towar wg sedziego"
    final = graph.invoke(Command(resume="approve"), config)
    assert final["booking"].booking_id == "MOCK-FV/1"


def test_pl_invoice_skips_reason_exception(tmp_path):
    # Sedzia, ktory by "zepsul" klasyfikacje, NIE powinien byc wolany dla PL.
    poison = Classification(
        treatment=TaxTreatment.INNE, country_bucket=CountryBucket.PL, confidence=0.1
    )
    graph = build_invoice_graph(
        extractor=StubExtractor(_invoice()),
        ledger=Ledger(tmp_path / "l.jsonl"),
        sink=MockSubiektSink(),
        reasoner=StubExceptionReasoner(poison),
        clock=lambda: "2026-06-01T10:00:00",
    )
    config = {"configurable": {"thread_id": "p1"}}
    paused = graph.invoke({"document": _doc(), "errors": []}, config)
    assert paused["classification"].treatment == TaxTreatment.KRAJOWA  # sedzia NIE wolany
