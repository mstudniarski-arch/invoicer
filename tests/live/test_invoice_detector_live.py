import os
from datetime import datetime
from pathlib import Path

import pytest

from invoicer.adapters.claude_detector import ClaudeInvoiceDetector
from invoicer.models import InvoiceDocument

_FIXTURE = Path(__file__).parent / "fixtures" / "sample_invoice.pdf"

pytestmark = pytest.mark.skipif(
    not os.getenv("ANTHROPIC_API_KEY") or not _FIXTURE.exists(),
    reason="wymaga ANTHROPIC_API_KEY oraz tests/live/fixtures/sample_invoice.pdf (test live)",
)


def test_detects_real_invoice_as_invoice():
    doc = InvoiceDocument(
        sender="a@b.pl",
        received_at=datetime(2026, 6, 23),
        filename="sample_invoice.pdf",
        content=_FIXTURE.read_bytes(),
    )
    assert ClaudeInvoiceDetector().is_invoice(doc) is True
