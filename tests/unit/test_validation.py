from datetime import date
from decimal import Decimal

from invoicer.models import Invoice, LineItem, Party
from invoicer.validation import nip_checksum_valid, totals_consistent


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
