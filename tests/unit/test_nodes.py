from datetime import date, datetime
from decimal import Decimal

from invoicer.adapters.mock_subiekt import MockSubiektSink
from invoicer.adapters.stub_extractor import StubExtractor
from invoicer.graph.nodes import (
    classify_node,
    make_book_node,
    make_extract_node,
    make_validate_node,
    route_after_review,
)
from invoicer.ledger import Ledger, LedgerEntry
from invoicer.models import (
    CheckStatus,
    Classification,
    CountryBucket,
    Invoice,
    InvoiceDocument,
    LineItem,
    Party,
    TaxTreatment,
)


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


def _foreign_invoice() -> Invoice:
    inv = _invoice()
    inv.seller = Party(name="Foreign Ltd", country="GB", vat_id="GB123")
    inv.seller.nip = None
    inv.total_vat = Decimal("0.00")
    inv.total_gross = Decimal("1000.00")
    inv.currency = "GBP"
    inv.lines[0].vat = Decimal("0.00")
    inv.lines[0].vat_rate = Decimal("0.00")
    inv.lines[0].gross = Decimal("1000.00")
    return inv


def test_classify_domestic_pl():
    update = classify_node({"invoice": _invoice()})
    c = update["classification"]
    assert c.country_bucket == CountryBucket.PL
    assert c.treatment == TaxTreatment.KRAJOWA
    assert c.human_must_confirm == []


def test_classify_non_eu_uk_no_vat():
    update = classify_node({"invoice": _foreign_invoice()})
    c = update["classification"]
    assert c.country_bucket == CountryBucket.POZA_UE
    assert c.treatment == TaxTreatment.IMPORT_USLUG
    assert c.human_must_confirm  # czlowiek musi potwierdzic
    assert "GBP" in c.currency_note


def test_classify_eu_foreign_de():
    inv = _foreign_invoice()
    inv.seller.country = "DE"
    update = classify_node({"invoice": inv})
    assert update["classification"].country_bucket == CountryBucket.UE
    assert update["classification"].treatment == TaxTreatment.IMPORT_USLUG


def test_route_after_review_approve_goes_to_book():
    assert route_after_review({"human_decision": "approve"}) == "book"


def test_route_after_review_reject_goes_to_end():
    assert route_after_review({"human_decision": "reject"}) == "end"
    assert route_after_review({}) == "end"


def test_book_node_posts_and_records_ledger(tmp_path):
    ledger = Ledger(tmp_path / "l.jsonl")
    node = make_book_node(MockSubiektSink(), ledger, clock=lambda: "2026-06-01T10:00:00")
    inv = _invoice()
    classification = Classification(treatment=TaxTreatment.KRAJOWA, country_bucket=CountryBucket.PL)
    update = node({"invoice": inv, "classification": classification})
    assert update["booking"].booking_id == "MOCK-FV/1"
    assert ledger.is_duplicate(inv.number, inv.seller.nip, inv.seller.name) is True
    entry = ledger.entries()[0]
    assert entry.booked_at == "2026-06-01T10:00:00"
    assert entry.booking_id == "MOCK-FV/1"
