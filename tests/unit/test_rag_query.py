from datetime import date
from decimal import Decimal

from invoicer.models import Invoice, LineItem, Party
from invoicer.rag.query import build_retrieval_query


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


def test_query_includes_allowlist_fields():
    q = build_retrieval_query(_foreign_invoice())
    assert "GB" in q  # kraj sprzedawcy
    assert "GBP" in q  # waluta
    assert "Subskrypcja SaaS" in q  # opis pozycji
    assert "brak" in q  # VAT na fakturze: brak


def test_query_excludes_buyer_and_address_pii():
    q = build_retrieval_query(_foreign_invoice())
    assert "Tajny Nabywca" not in q
    assert "Sekretna 9" not in q
    assert "London Str 1" not in q
