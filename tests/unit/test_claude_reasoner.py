from datetime import date
from decimal import Decimal

from invoicer.adapters.claude_reasoner import REASON_PROMPT, build_reason_message
from invoicer.models import Invoice, LineItem, Party


def _foreign_invoice() -> Invoice:
    line = LineItem(
        description="Subskrypcja SaaS",
        quantity=Decimal("1"),
        unit_net=Decimal("1000.00"),
        vat_rate=Decimal("0.00"),
        net=Decimal("1000.00"),
        vat=Decimal("0.00"),
        gross=Decimal("1000.00"),
    )
    return Invoice(
        seller=Party(name="Foreign Ltd", country="GB", vat_id="GB123", address="London Str 1"),
        buyer=Party(name="Tajny Nabywca", nip="5260001246", country="PL", address="Sekretna 9"),
        number="INV/7",
        issue_date=date(2026, 6, 1),
        currency="GBP",
        lines=[line],
        total_net=Decimal("1000.00"),
        total_vat=Decimal("0.00"),
        total_gross=Decimal("1000.00"),
    )


def test_message_text_includes_allowlist_fields():
    text = build_reason_message(_foreign_invoice()).content
    assert REASON_PROMPT in text
    assert "GB" in text  # kraj sprzedawcy
    assert "GBP" in text  # waluta
    assert "Subskrypcja SaaS" in text  # opis pozycji (usluga vs towar)


def test_message_does_not_leak_buyer_pii():
    text = build_reason_message(_foreign_invoice()).content
    assert "Tajny Nabywca" not in text  # nazwa nabywcy
    assert "Sekretna 9" not in text  # adres nabywcy
    assert "London Str 1" not in text  # adres sprzedawcy


def test_prompt_has_injection_defense():
    assert "DANE" in REASON_PROMPT
    assert "instrukcje" in REASON_PROMPT.lower()
