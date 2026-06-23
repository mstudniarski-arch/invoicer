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


class _FakeStructured:
    def __init__(self, result):
        self._result = result

    def invoke(self, _messages):
        return self._result


class _FakeLLM:
    def __init__(self, result):
        self._result = result

    def with_structured_output(self, _schema):
        return _FakeStructured(self._result)


def test_claude_detector_returns_true_for_invoice():
    from invoicer.adapters.claude_detector import ClaudeInvoiceDetector, InvoiceCheck

    llm = _FakeLLM(InvoiceCheck(is_invoice=True, reason="naglowek FAKTURA, NIP, pozycje"))
    assert ClaudeInvoiceDetector(llm=llm).is_invoice(_doc()) is True


def test_claude_detector_returns_false_for_non_invoice():
    from invoicer.adapters.claude_detector import ClaudeInvoiceDetector, InvoiceCheck

    llm = _FakeLLM(InvoiceCheck(is_invoice=False, reason="to CV, nie faktura"))
    assert ClaudeInvoiceDetector(llm=llm).is_invoice(_doc()) is False


def test_claude_detector_satisfies_protocol():
    from invoicer.adapters.claude_detector import ClaudeInvoiceDetector

    assert isinstance(ClaudeInvoiceDetector(llm=_FakeLLM(None)), InvoiceDetector)


def test_detection_message_has_text_and_pdf_block():
    from invoicer.adapters.claude_detector import build_detection_message

    blocks = build_detection_message(_doc()).content
    assert blocks[0]["type"] == "text"
    assert blocks[1]["type"] == "file"
    assert blocks[1]["mime_type"] == "application/pdf"
