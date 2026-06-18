from datetime import datetime

from invoicer.models import InvoiceDocument, ValidationResult


def test_invoice_document_holds_raw_attachment():
    doc = InvoiceDocument(
        sender="ksiegowa@klient.pl",
        subject="Faktura 06/2026",
        received_at=datetime(2026, 6, 1, 10, 0, 0),
        filename="faktura.pdf",
        content=b"%PDF-1.4 dane",
    )
    assert doc.sender == "ksiegowa@klient.pl"
    assert doc.filename == "faktura.pdf"
    assert doc.content.startswith(b"%PDF")


def test_invoice_document_subject_optional():
    doc = InvoiceDocument(
        sender="a@b.pl",
        received_at=datetime(2026, 1, 1, 0, 0, 0),
        filename="x.pdf",
        content=b"x",
    )
    assert doc.subject == ""


def test_validation_result_is_duplicate_defaults_false():
    vr = ValidationResult(checks=[])
    assert vr.is_duplicate is False
