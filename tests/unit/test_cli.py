from datetime import date, datetime
from decimal import Decimal

from invoicer.adapters.mock_subiekt import MockSubiektSink
from invoicer.adapters.stub_extractor import StubExtractor
from invoicer.cli import process_document
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


def _foreign_invoice() -> Invoice:
    inv = _invoice()
    inv.seller = Party(name="Foreign Ltd", country="GB", vat_id="GB123")
    inv.total_vat = Decimal("0.00")
    inv.total_gross = Decimal("1000.00")
    inv.currency = "GBP"
    inv.lines[0].vat = Decimal("0.00")
    inv.lines[0].vat_rate = Decimal("0.00")
    inv.lines[0].gross = Decimal("1000.00")
    return inv


def _doc() -> InvoiceDocument:
    return InvoiceDocument(
        sender="a@b.pl", received_at=datetime(2026, 6, 1), filename="x.pdf", content=b"%PDF"
    )


def _graph(invoice, ledger):
    return build_invoice_graph(
        extractor=StubExtractor(invoice),
        ledger=ledger,
        sink=MockSubiektSink(),
        clock=lambda: "2026-06-01T10:00:00",
    )


def test_process_document_approve_books(tmp_path):
    seen = {}

    def decide(payload):
        seen.update(payload)
        return "approve"

    final = process_document(
        _graph(_invoice(), Ledger(tmp_path / "l.jsonl")), _doc(), thread_id="c1", decide=decide
    )
    assert seen["number"] == "FV/1"  # driver przekazal podsumowanie do decyzji
    assert final["booking"].booking_id == "MOCK-FV/1"


def test_process_document_reject_does_not_book(tmp_path):
    final = process_document(
        _graph(_invoice(), Ledger(tmp_path / "l.jsonl")),
        _doc(),
        thread_id="c2",
        decide=lambda p: "reject",
    )
    assert final.get("booking") is None


def test_process_document_foreign_payload_reaches_human(tmp_path):
    seen = {}

    def decide(payload):
        seen.update(payload)
        return "reject"

    process_document(
        _graph(_foreign_invoice(), Ledger(tmp_path / "l.jsonl")),
        _doc(),
        thread_id="c3",
        decide=decide,
    )
    assert seen["country"] == "GB"
    assert seen["treatment"] == "import_uslug"
    assert seen["must_confirm"]  # niepusta lista do potwierdzenia
