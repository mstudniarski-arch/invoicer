import base64
from datetime import datetime

from invoicer.adapters.claude_extractor import EXTRACTION_PROMPT, build_extraction_message
from invoicer.models import InvoiceDocument


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
