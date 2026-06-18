from datetime import date, datetime
from decimal import Decimal

from langgraph.types import Command

from invoicer.adapters.mock_subiekt import MockSubiektSink
from invoicer.adapters.stub_extractor import StubExtractor
from invoicer.graph.build import build_invoice_graph
from invoicer.ledger import Ledger
from invoicer.models import Invoice, InvoiceDocument, LineItem, Party


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
