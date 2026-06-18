from datetime import date
from decimal import Decimal

from invoicer.ledger import Ledger, LedgerEntry
from invoicer.models import CheckStatus, Invoice, LineItem, Party
from invoicer.validation import validate_invoice


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
        number="FV/2026/06/01",
        issue_date=date(2026, 6, 1),
        currency="PLN",
        lines=[line],
        total_net=Decimal("1000.00"),
        total_vat=Decimal("230.00"),
        total_gross=Decimal("1230.00"),
    )


def _ledger_with_invoice(tmp_path, invoice: Invoice) -> Ledger:
    ledger = Ledger(tmp_path / "ledger.jsonl")
    ledger.append(
        LedgerEntry(
            number=invoice.number,
            seller_nip=invoice.seller.nip,
            seller_name=invoice.seller.name,
            total_gross=str(invoice.total_gross),
            booking_id="MOCK-1",
            booked_at="2026-06-01T10:00:00",
        )
    )
    return ledger


def test_no_ledger_means_no_duplicate_check():
    vr = validate_invoice(_invoice())
    assert vr.is_duplicate is False
    assert {c.name for c in vr.checks} == {"nip", "sums", "lines"}


def test_ledger_without_match_passes_duplicate_check(tmp_path):
    ledger = Ledger(tmp_path / "empty.jsonl")
    vr = validate_invoice(_invoice(), ledger=ledger)
    assert vr.is_duplicate is False
    dup = next(c for c in vr.checks if c.name == "duplicate")
    assert dup.status == CheckStatus.PASS
    assert vr.ok is True


def test_duplicate_invoice_fails(tmp_path):
    inv = _invoice()
    ledger = _ledger_with_invoice(tmp_path, inv)
    vr = validate_invoice(inv, ledger=ledger)
    assert vr.is_duplicate is True
    dup = next(c for c in vr.checks if c.name == "duplicate")
    assert dup.status == CheckStatus.FAIL
    assert vr.ok is False
