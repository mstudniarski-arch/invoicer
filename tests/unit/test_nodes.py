from datetime import date, datetime
from decimal import Decimal

from invoicer.adapters.stub_extractor import StubExtractor
from invoicer.graph.nodes import make_extract_node, make_validate_node
from invoicer.ledger import Ledger, LedgerEntry
from invoicer.models import CheckStatus, Invoice, InvoiceDocument, LineItem, Party


def _invoice(confidence=0.95) -> Invoice:
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
        extraction_confidence=confidence,
    )


def _doc() -> InvoiceDocument:
    return InvoiceDocument(
        sender="a@b.pl", received_at=datetime(2026, 6, 1), filename="x.pdf", content=b"%PDF"
    )


def test_extract_node_sets_invoice_and_attempts():
    node = make_extract_node(StubExtractor(_invoice()))
    update = node({"document": _doc()})
    assert update["invoice"].number == "FV/1"
    assert update["extract_attempts"] == 1
    assert "errors" not in update  # wysoka pewnosc -> brak flagi


def test_extract_node_flags_low_confidence():
    node = make_extract_node(StubExtractor(_invoice(confidence=0.3)))
    update = node({"document": _doc()})
    assert update["errors"] and "pewnosc" in update["errors"][0].lower()


def test_validate_node_runs_validation_with_ledger(tmp_path):
    node = make_validate_node(Ledger(tmp_path / "l.jsonl"))
    update = node({"invoice": _invoice()})
    assert update["validation"].ok is True
    assert {c.name for c in update["validation"].checks} == {"nip", "sums", "lines", "duplicate"}


def test_extract_node_accumulates_attempts():
    node = make_extract_node(StubExtractor(_invoice()))
    update = node({"document": _doc(), "extract_attempts": 3})
    assert update["extract_attempts"] == 4  # odczyt biezacego (3) + 1 = wartosc absolutna


def test_validate_node_flags_duplicate(tmp_path):
    ledger = Ledger(tmp_path / "l.jsonl")
    inv = _invoice()
    ledger.append(
        LedgerEntry(
            number=inv.number,
            seller_nip=inv.seller.nip,
            seller_name=inv.seller.name,
            total_gross=str(inv.total_gross),
            booking_id="MOCK-1",
            booked_at="2026-06-01T10:00:00",
        )
    )
    update = make_validate_node(ledger)({"invoice": inv})
    assert update["validation"].is_duplicate is True
    assert update["validation"].ok is False
    dup = next(c for c in update["validation"].checks if c.name == "duplicate")
    assert dup.status == CheckStatus.FAIL
