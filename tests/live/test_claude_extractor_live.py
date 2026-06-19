import os
from pathlib import Path

import pytest

from invoicer.adapters.claude_extractor import ClaudeVisionExtractor
from invoicer.models import Invoice, InvoiceDocument

_FIXTURE = Path(__file__).parent / "fixtures" / "sample_invoice.pdf"

pytestmark = pytest.mark.skipif(
    not os.getenv("ANTHROPIC_API_KEY") or not _FIXTURE.exists(),
    reason="wymaga ANTHROPIC_API_KEY oraz tests/live/fixtures/sample_invoice.pdf (test live)",
)


def test_real_claude_extracts_invoice_from_pdf():
    from datetime import datetime

    doc = InvoiceDocument(
        sender="a@b.pl",
        received_at=datetime(2026, 6, 1),
        filename="sample_invoice.pdf",
        content=_FIXTURE.read_bytes(),
    )
    invoice = ClaudeVisionExtractor().extract(doc)
    assert isinstance(invoice, Invoice)
    assert invoice.number  # niepuste
    assert invoice.total_gross > 0
