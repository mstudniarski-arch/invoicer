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


def _payload(*, treatment: str | None = None, due_date: date | None = None) -> BookingPayload:
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
        due_date=due_date,
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
    # Faktura kosztowa: my (payload.buyer) -> seller_* (sekcja Nabywca),
    # dostawca (payload.seller) -> buyer_* (sekcja Sprzedawca). Patrz test swapu nizej.
    assert inv["seller_name"] == "My"
    assert inv["seller_tax_no"] == "1234567890"
    assert inv["buyer_name"] == "Dostawca"
    assert inv["currency"] == "PLN"
    assert inv["positions"] == [
        {"name": "Usluga", "quantity": "2", "total_price_gross": "2460.00", "tax": 23}
    ]
    assert result.booking_id == "FZ/2026/1"
    assert result.sink == "fakturownia"
    assert result.status == "posted"


def test_cost_invoice_swaps_parties_for_correct_display():
    # Faktura kosztowa (income=0): Fakturownia renderuje pola seller_* w sekcji "Nabywca",
    # a buyer_* w sekcji "Sprzedawca". Wiec nasza firma (payload.buyer) musi trafic do
    # seller_*, a dostawca z PDF (payload.seller) do buyer_* — inaczej na wydruku
    # sprzedawca i nabywca sa zamienieni.
    sink, client = _sink(_FakeResponse(201, {"id": 1, "number": "X"}))
    sink.post(_payload())  # seller="Dostawca"/5260001246, buyer="My"/1234567890
    inv = client.calls[0][1]["invoice"]
    # nasza firma -> seller_* (wyswietli sie jako Nabywca)
    assert inv["seller_name"] == "My"
    assert inv["seller_tax_no"] == "1234567890"
    assert inv["seller_country"] == "PL"
    # dostawca z PDF -> buyer_* (wyswietli sie jako Sprzedawca)
    assert inv["buyer_name"] == "Dostawca"
    assert inv["buyer_tax_no"] == "5260001246"
    assert inv["buyer_country"] == "PL"


def test_department_id_replaces_seller_fields():
    # Gdy podany department_id (nasza firma jako ISTNIEJACY dzial w Fakturowni), wysylamy
    # department_id zamiast seller_* — inaczej Fakturownia probuje utworzyc nowy dzial i przy
    # wlaczonym zabezpieczeniu konta zwraca 422 "nie pozwala na utworzenie dzialu".
    client = _FakeClient(_FakeResponse(201, {"id": 1, "number": "X"}))
    sink = FakturowniaSink(client, domain="acme", api_token="TKN", department_id=2010019)
    sink.post(_payload())
    inv = client.calls[0][1]["invoice"]
    assert inv["department_id"] == 2010019
    assert "seller_name" not in inv
    assert "seller_tax_no" not in inv
    assert "seller_country" not in inv
    # dostawca z PDF nadal jako buyer_* (sekcja Sprzedawca)
    assert inv["buyer_name"] == "Dostawca"
    assert inv["buyer_tax_no"] == "5260001246"


def test_number_sent_from_invoice():
    # Numer faktury kosztowej musi byc identyczny jak na PDF (payload.number),
    # inaczej Fakturownia nie ma numeru i UI pokazuje pusty numer ("- - -").
    sink, client = _sink(_FakeResponse(201, {"id": 1, "number": "X"}))
    sink.post(_payload())
    assert client.calls[0][1]["invoice"]["number"] == "FZ/1"


def test_payment_to_set_from_due_date():
    # Termin platnosci musi trafic do Fakturowni z faktury (payment_to), a nie byc
    # liczony przez Fakturownie z issue_date + domyslny termin konta.
    sink, client = _sink(_FakeResponse(201, {"id": 1, "number": "X"}))
    sink.post(_payload(due_date=date(2021, 2, 10)))
    assert client.calls[0][1]["invoice"]["payment_to"] == "2021-02-10"


def test_payment_to_absent_when_no_due_date():
    # Brak terminu na fakturze -> nie wysylamy payment_to (Fakturownia uzyje swojego domyslnego).
    sink, client = _sink(_FakeResponse(201, {"id": 1, "number": "X"}))
    sink.post(_payload(due_date=None))
    assert "payment_to" not in client.calls[0][1]["invoice"]


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


def test_post_raises_when_response_has_no_number_or_id():
    sink, _ = _sink(_FakeResponse(200, {"unexpected": "shape"}))
    with pytest.raises(FakturowniaError):
        sink.post(_payload())


def test_tax_no_falls_back_to_vat_id_for_foreign_supplier():
    # Dostawca z PDF trafia w buyer_* (faktura kosztowa), wiec fallback nip->vat_id
    # ma dzialac na buyer_tax_no.
    sink, client = _sink(_FakeResponse(201, {"id": 1, "number": "X"}))
    payload = _payload()
    payload.seller = Party(name="Foreign Ltd", country="DE", vat_id="DE123456789")  # nip=None
    sink.post(payload)
    assert client.calls[0][1]["invoice"]["buyer_tax_no"] == "DE123456789"


def test_positions_maps_all_lines():
    sink, client = _sink(_FakeResponse(201, {"id": 1, "number": "X"}))
    payload = _payload()
    payload.lines.append(
        LineItem(
            description="Druga",
            quantity=Decimal("3"),
            unit_net=Decimal("50.00"),
            vat_rate=Decimal("0.08"),
            net=Decimal("150.00"),
            vat=Decimal("12.00"),
            gross=Decimal("162.00"),
        )
    )
    sink.post(payload)
    positions = client.calls[0][1]["invoice"]["positions"]
    assert len(positions) == 2
    assert positions[1] == {
        "name": "Druga",
        "quantity": "3",
        "total_price_gross": "162.00",
        "tax": 8,
    }
