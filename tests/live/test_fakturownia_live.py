import os
from datetime import date
from decimal import Decimal

import pytest

from invoicer.adapters.fakturownia import build_fakturownia_sink
from invoicer.booking import BookingPayload
from invoicer.models import LineItem, Party

pytestmark = pytest.mark.skipif(
    not (os.getenv("FAKTUROWNIA_API_TOKEN") and os.getenv("FAKTUROWNIA_DOMAIN")),
    reason="wymaga FAKTUROWNIA_API_TOKEN + FAKTUROWNIA_DOMAIN (test live)",
)


def test_creates_cost_invoice_live():
    line = LineItem(
        description="Usluga testowa (live)",
        quantity=Decimal("1"),
        unit_net=Decimal("100.00"),
        vat_rate=Decimal("0.23"),
        net=Decimal("100.00"),
        vat=Decimal("23.00"),
        gross=Decimal("123.00"),
    )
    payload = BookingPayload(
        seller=Party(name="Dostawca Test", nip="5260001246", country="PL"),
        buyer=Party(name="Nabywca Test", nip="1234567890", country="PL"),
        number="FZ/LIVE/1",
        currency="PLN",
        lines=[line],
        total_net=Decimal("100.00"),
        total_vat=Decimal("23.00"),
        total_gross=Decimal("123.00"),
        treatment="krajowa",
        issue_date=date(2026, 6, 1),
    )
    result = build_fakturownia_sink().post(payload)
    assert result.booking_id  # niepuste — Fakturownia nadala numer/id
    assert result.sink == "fakturownia"
