import logging
from datetime import date
from decimal import Decimal

from invoicer.adapters.mock_subiekt import MockSubiektSink
from invoicer.booking import BookingResult, invoice_to_booking_payload
from invoicer.models import Invoice, LineItem, Party
from invoicer.ports import AccountingSink


def _payload():
    line = LineItem(
        description="Usluga",
        quantity=Decimal("1"),
        unit_net=Decimal("1000.00"),
        vat_rate=Decimal("0.23"),
        net=Decimal("1000.00"),
        vat=Decimal("230.00"),
        gross=Decimal("1230.00"),
    )
    invoice = Invoice(
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
    return invoice_to_booking_payload(invoice)


def test_mock_subiekt_satisfies_accounting_sink_protocol():
    assert isinstance(MockSubiektSink(), AccountingSink)


def test_post_returns_deterministic_booking_result():
    res = MockSubiektSink().post(_payload())
    assert isinstance(res, BookingResult)
    assert res.booking_id == "MOCK-FV/2026/06/01"
    assert res.status == "posted"
    assert res.sink == "mock-subiekt"


def test_post_logs_the_decree(caplog):
    with caplog.at_level(logging.INFO, logger="invoicer.mock_subiekt"):
        MockSubiektSink().post(_payload())
    assert "FV/2026/06/01" in caplog.text
