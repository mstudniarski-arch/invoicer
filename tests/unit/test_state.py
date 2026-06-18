from datetime import date, datetime
from decimal import Decimal

from invoicer.adapters.stub_extractor import StubExtractor
from invoicer.models import Invoice, InvoiceDocument, LineItem, Party
from invoicer.ports import InvoiceExtractor
from invoicer.state import InvoiceState


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
        number="FV/1",
        issue_date=date(2026, 6, 1),
        currency="PLN",
        lines=[line],
        total_net=Decimal("1000.00"),
        total_vat=Decimal("230.00"),
        total_gross=Decimal("1230.00"),
    )


def _doc() -> InvoiceDocument:
    return InvoiceDocument(
        sender="a@b.pl",
        received_at=datetime(2026, 6, 1, 10, 0, 0),
        filename="x.pdf",
        content=b"%PDF",
    )


def test_invoice_state_accepts_partial_dict():
    state: InvoiceState = {"document": _doc(), "errors": []}
    assert state["document"].filename == "x.pdf"


def test_stub_extractor_satisfies_protocol():
    assert isinstance(StubExtractor(_invoice()), InvoiceExtractor)


def test_stub_extractor_returns_independent_copy():
    inv = _invoice()
    extractor = StubExtractor(inv)
    out = extractor.extract(_doc())
    out.seller.name = "ZMIENIONE"
    assert inv.seller.name == "ACME"  # stub zwraca niezalezna kopie
    assert out.number == "FV/1"
