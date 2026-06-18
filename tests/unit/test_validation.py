from datetime import date
from decimal import Decimal

from invoicer.models import CheckStatus, Invoice, LineItem, Party
from invoicer.validation import nip_checksum_valid, totals_consistent, validate_invoice


def test_valid_nip_plain():
    assert nip_checksum_valid("5260001246") is True


def test_valid_nip_with_formatting():
    assert nip_checksum_valid("526-000-12-46") is True


def test_invalid_nip_bad_checksum():
    assert nip_checksum_valid("5260001247") is False


def test_invalid_nip_wrong_length():
    assert nip_checksum_valid("12345") is False


def test_invalid_nip_control_equals_ten():
    # Pierwsze 9 cyfr daje sume wazona ≡ 10 mod 11 → NIP niepoprawny z definicji.
    assert nip_checksum_valid("9000000001") is False


def test_none_nip_is_invalid():
    assert nip_checksum_valid(None) is False


def _invoice(total_net, total_vat, total_gross) -> Invoice:
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
        lines=[line],
        total_net=Decimal(total_net),
        total_vat=Decimal(total_vat),
        total_gross=Decimal(total_gross),
    )


def test_totals_consistent_true():
    assert totals_consistent(_invoice("1000.00", "230.00", "1230.00")) is True


def test_totals_within_grosz_tolerance():
    assert totals_consistent(_invoice("1000.00", "230.00", "1230.01")) is True


def test_totals_inconsistent_gross():
    assert totals_consistent(_invoice("1000.00", "230.00", "1300.00")) is False


def test_totals_inconsistent_with_lines_sum():
    assert totals_consistent(_invoice("999.00", "230.00", "1229.00")) is False


def test_validate_invoice_all_pass():
    vr = validate_invoice(_invoice("1000.00", "230.00", "1230.00"))
    assert vr.ok is True
    assert {c.name for c in vr.checks} == {"nip", "sums", "lines"}


def test_validate_invoice_bad_nip_fails():
    inv = _invoice("1000.00", "230.00", "1230.00")
    inv.seller.nip = "5260001247"  # zla suma kontrolna
    vr = validate_invoice(inv)
    assert vr.ok is False
    nip_check = next(c for c in vr.checks if c.name == "nip")
    assert nip_check.status == CheckStatus.FAIL


def test_validate_invoice_foreign_seller_nip_warn():
    inv = _invoice("1000.00", "230.00", "1230.00")
    inv.seller.country = "GB"
    inv.seller.nip = None
    vr = validate_invoice(inv)
    nip_check = next(c for c in vr.checks if c.name == "nip")
    assert nip_check.status == CheckStatus.WARN
    assert vr.ok is True  # zagraniczny brak NIP nie jest twardym bledem


def test_validate_invoice_inconsistent_sums_fails():
    vr = validate_invoice(_invoice("1000.00", "230.00", "1300.00"))
    assert vr.ok is False
    sums_check = next(c for c in vr.checks if c.name == "sums")
    assert sums_check.status == CheckStatus.FAIL
