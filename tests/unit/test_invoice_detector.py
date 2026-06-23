from datetime import datetime

from invoicer.adapters.stub_detector import StubInvoiceDetector
from invoicer.models import InvoiceDocument
from invoicer.ports import InvoiceDetector


def _doc() -> InvoiceDocument:
    return InvoiceDocument(
        sender="a@b.pl", received_at=datetime(2026, 6, 23), filename="x.pdf", content=b"%PDF"
    )


def test_stub_returns_configured_result():
    assert StubInvoiceDetector(result=True).is_invoice(_doc()) is True
    assert StubInvoiceDetector(result=False).is_invoice(_doc()) is False


def test_stub_defaults_true():
    assert StubInvoiceDetector().is_invoice(_doc()) is True


def test_stub_satisfies_invoice_detector_protocol():
    assert isinstance(StubInvoiceDetector(), InvoiceDetector)
