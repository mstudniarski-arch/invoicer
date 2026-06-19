from datetime import date
from decimal import Decimal

from invoicer.extraction import (
    InvoiceExtraction,
    LineItemExtraction,
    PartyExtraction,
    extraction_to_invoice,
)
from invoicer.models import Invoice


def _extraction() -> InvoiceExtraction:
    return InvoiceExtraction(
        seller=PartyExtraction(name="ACME", nip="5260001246", country="PL"),
        buyer=PartyExtraction(name="Klient", country="PL"),
        number="FV/2026/06/01",
        issue_date="2026-06-01",
        currency="PLN",
        lines=[
            LineItemExtraction(
                description="Usluga",
                quantity="1",
                unit_net="1000.00",
                vat_rate="0.23",
                net="1000.00",
                vat="230.00",
                gross="1230.00",
            )
        ],
        total_net="1000.00",
        total_vat="230.00",
        total_gross="1230.00",
        confidence=0.9,
    )


def test_mapper_produces_domain_invoice_with_decimals():
    inv = extraction_to_invoice(_extraction())
    assert isinstance(inv, Invoice)
    assert inv.number == "FV/2026/06/01"
    assert inv.issue_date == date(2026, 6, 1)
    assert inv.total_gross == Decimal("1230.00")
    assert inv.lines[0].vat == Decimal("230.00")
    assert inv.seller.nip == "5260001246"
    assert inv.extraction_confidence == 0.9


def test_mapper_handles_optional_dates():
    ex = _extraction()
    ex.sale_date = "2026-06-02"
    ex.due_date = None
    inv = extraction_to_invoice(ex)
    assert inv.sale_date == date(2026, 6, 2)
    assert inv.due_date is None


def test_confidence_is_bounded_0_1():
    import pytest
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        InvoiceExtraction(
            seller=PartyExtraction(name="A"),
            buyer=PartyExtraction(name="B"),
            number="X",
            issue_date="2026-01-01",
            lines=[],
            total_net="0",
            total_vat="0",
            total_gross="0",
            confidence=1.5,
        )
