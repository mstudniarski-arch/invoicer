import base64
from datetime import datetime
from decimal import Decimal

import pytest

from invoicer.adapters.claude_extractor import (
    EXTRACTION_PROMPT,
    ClaudeVisionExtractor,
    build_extraction_message,
)
from invoicer.extraction import InvoiceExtraction, LineItemExtraction, PartyExtraction
from invoicer.models import Invoice, InvoiceDocument
from invoicer.ports import InvoiceExtractor


def _doc(filename: str, content: bytes = b"%PDF-1.4 dane") -> InvoiceDocument:
    return InvoiceDocument(
        sender="a@b.pl", received_at=datetime(2026, 6, 1), filename=filename, content=content
    )


def test_message_has_text_and_pdf_file_block():
    msg = build_extraction_message(_doc("faktura.pdf"))
    blocks = msg.content
    assert blocks[0]["type"] == "text"
    assert blocks[0]["text"] == EXTRACTION_PROMPT
    assert blocks[1]["type"] == "file"
    assert blocks[1]["mime_type"] == "application/pdf"
    assert base64.b64decode(blocks[1]["base64"]) == b"%PDF-1.4 dane"


def test_scan_image_uses_image_block():
    msg = build_extraction_message(_doc("skan.png", content=b"\x89PNG"))
    assert msg.content[1]["type"] == "image"
    assert msg.content[1]["mime_type"] == "image/png"


def test_jpeg_scan_mime():
    msg = build_extraction_message(_doc("skan.jpg", content=b"\xff\xd8\xff"))
    assert msg.content[1]["mime_type"] == "image/jpeg"


def test_prompt_has_injection_defense():
    # tresc dokumentu jako DANE, nie instrukcje
    assert "DANE" in EXTRACTION_PROMPT
    assert "instrukcje" in EXTRACTION_PROMPT.lower()


def test_uppercase_pdf_extension_uses_file_block():
    msg = build_extraction_message(_doc("FAKTURA.PDF"))
    assert msg.content[1]["type"] == "file"
    assert msg.content[1]["mime_type"] == "application/pdf"


def test_jpeg_extension_uses_image_block():
    msg = build_extraction_message(_doc("skan.jpeg", content=b"\xff\xd8\xff"))
    assert msg.content[1]["type"] == "image"
    assert msg.content[1]["mime_type"] == "image/jpeg"


def test_unsupported_extension_raises():
    with pytest.raises(ValueError, match="Nieobslugiwany"):
        build_extraction_message(_doc("dokument.tiff"))


def _extraction() -> InvoiceExtraction:
    return InvoiceExtraction(
        seller=PartyExtraction(name="ACME", nip="5260001246", country="PL"),
        buyer=PartyExtraction(name="Klient", country="PL"),
        number="FV/1",
        issue_date="2026-06-01",
        currency="PLN",
        lines=[
            LineItemExtraction(
                description="Usluga",
                quantity="1",
                unit_net="1000.00",
                vat_rate="0.23",
                net="1000.00",
                vat="230.00",
                gross="1230.00",
            )
        ],
        total_net="1000.00",
        total_vat="230.00",
        total_gross="1230.00",
        confidence=0.9,
    )


class _FakeStructured:
    def __init__(self, result):
        self.result = result
        self.received = None

    def invoke(self, messages):
        self.received = messages
        return self.result


class _FakeLLM:
    def __init__(self, result):
        self.structured = _FakeStructured(result)
        self.schema = None

    def with_structured_output(self, schema):
        self.schema = schema
        return self.structured


def test_claude_extractor_satisfies_protocol():
    assert isinstance(ClaudeVisionExtractor(llm=_FakeLLM(None)), InvoiceExtractor)


def test_extract_uses_structured_output_and_maps_to_invoice():
    llm = _FakeLLM(_extraction())
    inv = ClaudeVisionExtractor(llm=llm).extract(_doc("faktura.pdf"))
    assert isinstance(inv, Invoice)
    assert inv.number == "FV/1"
    assert inv.total_gross == Decimal("1230.00")
    assert inv.extraction_confidence == 0.9
    # LLM zostal poproszony o structured output wg InvoiceExtraction, z multimodalna wiadomoscia
    assert llm.schema is InvoiceExtraction
    sent = llm.structured.received[0]
    assert any(b["type"] in ("file", "image") for b in sent.content)
