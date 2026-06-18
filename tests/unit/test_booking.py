from datetime import date
from decimal import Decimal

from invoicer.booking import BookingPayload, BookingResult, invoice_to_booking_payload
from invoicer.models import Invoice, LineItem, Party


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


def test_mapper_copies_core_fields():
    payload = invoice_to_booking_payload(_invoice())
    assert isinstance(payload, BookingPayload)
    assert payload.number == "FV/2026/06/01"
    assert payload.seller.name == "ACME"
    assert payload.total_gross == Decimal("1230.00")
    assert payload.currency == "PLN"
    assert payload.treatment is None


def test_mapper_carries_treatment_when_given():
    payload = invoice_to_booking_payload(_invoice(), treatment="import_uslug")
    assert payload.treatment == "import_uslug"


def test_booking_result_defaults_status_posted():
    res = BookingResult(booking_id="MOCK-1", sink="mock-subiekt")
    assert res.status == "posted"


def test_payload_is_independent_snapshot():
    invoice = _invoice()
    payload = invoice_to_booking_payload(invoice)
    payload.seller.name = "ZMIENIONE"
    payload.lines[0].gross = Decimal("9999.99")
    assert invoice.seller.name == "ACME"
    assert invoice.lines[0].gross == Decimal("1230.00")
