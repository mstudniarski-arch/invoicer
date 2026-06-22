from datetime import date
from decimal import Decimal

import pytest

from invoicer.adapters.fakturownia import FakturowniaError, FakturowniaSink
from invoicer.booking import BookingPayload
from invoicer.models import LineItem, Party
from invoicer.ports import AccountingSink


class _FakeResponse:
    def __init__(self, status_code: int, json_data: dict | None = None, text: str = "") -> None:
        self.status_code = status_code
        self._json = json_data or {}
        self.text = text

    def json(self) -> dict:
        return self._json


class _FakeClient:
    """Rejestruje wywolania i zwraca skonfigurowana odpowiedz (bez sieci)."""

    def __init__(self, response: _FakeResponse) -> None:
        self._response = response
        self.calls: list[tuple[str, dict]] = []

    def post(self, url: str, json: dict) -> _FakeResponse:
        self.calls.append((url, json))
        return self._response


def _payload(*, treatment: str | None = None) -> BookingPayload:
    line = LineItem(
        description="Usluga",
        quantity=Decimal("2"),
        unit_net=Decimal("1000.00"),
        vat_rate=Decimal("0.23"),
        net=Decimal("2000.00"),
        vat=Decimal("460.00"),
        gross=Decimal("2460.00"),
    )
    return BookingPayload(
        seller=Party(name="Dostawca", nip="5260001246", country="PL"),
        buyer=Party(name="My", nip="1234567890", country="PL"),
        number="FZ/1",
        currency="PLN",
        lines=[line],
        total_net=Decimal("2000.00"),
        total_vat=Decimal("460.00"),
        total_gross=Decimal("2460.00"),
        treatment=treatment,
        issue_date=date(2026, 6, 1),
    )


def _sink(response: _FakeResponse) -> tuple[FakturowniaSink, _FakeClient]:
    client = _FakeClient(response)
    return FakturowniaSink(client, domain="acme", api_token="TKN"), client


def test_post_builds_cost_invoice_and_returns_booking_result():
    sink, client = _sink(_FakeResponse(201, {"id": 9, "number": "FZ/2026/1"}))
    result = sink.post(_payload())
    url, body = client.calls[0]
    assert url == "https://acme.fakturownia.pl/invoices.json"
    assert body["api_token"] == "TKN"
    inv = body["invoice"]
    assert inv["income"] == 0
    assert inv["kind"] == "vat"
    assert inv["issue_date"] == "2026-06-01"
    assert inv["seller_name"] == "Dostawca"
    assert inv["seller_tax_no"] == "5260001246"
    assert inv["buyer_name"] == "My"
    assert inv["currency"] == "PLN"
    assert inv["positions"] == [
        {"name": "Usluga", "quantity": "2", "price_net": "1000.00", "tax": 23}
    ]
    assert result.booking_id == "FZ/2026/1"
    assert result.sink == "fakturownia"
    assert result.status == "posted"


def test_post_falls_back_to_id_when_no_number():
    sink, _ = _sink(_FakeResponse(201, {"id": 7}))
    assert sink.post(_payload()).booking_id == "7"


def test_post_raises_and_redacts_pii_on_error():
    body_text = "blad: NIP 5260001246 nieprawidlowy, kontakt ksiegowa@firma.pl"
    sink, _ = _sink(_FakeResponse(422, text=body_text))
    with pytest.raises(FakturowniaError) as exc:
        sink.post(_payload())
    msg = str(exc.value)
    assert "422" in msg
    assert "5260001246" not in msg
    assert "ksiegowa@firma.pl" not in msg
    assert "[NIP]" in msg
    assert "[EMAIL]" in msg


def test_reverse_charge_set_for_import_uslug_not_for_krajowa():
    sink, client = _sink(_FakeResponse(201, {"id": 1, "number": "X"}))
    sink.post(_payload(treatment="import_uslug"))
    assert client.calls[0][1]["invoice"]["reverse_charge"] is True

    sink2, client2 = _sink(_FakeResponse(201, {"id": 2, "number": "Y"}))
    sink2.post(_payload(treatment="krajowa"))
    assert client2.calls[0][1]["invoice"]["reverse_charge"] is False


def test_conforms_to_accounting_sink_protocol():
    sink, _ = _sink(_FakeResponse(201, {"id": 1}))
    assert isinstance(sink, AccountingSink)
