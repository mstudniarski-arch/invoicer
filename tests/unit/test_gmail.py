import base64

from invoicer.adapters.gmail import (
    GMAIL_SCOPES,
    _b64url_decode,
    _build_query,
    _header,
    _iter_pdf_parts,
)


def test_scope_is_readonly():
    assert GMAIL_SCOPES == ["https://www.googleapis.com/auth/gmail.readonly"]


def test_build_query_filters_sender_and_pdf_attachments():
    assert _build_query("a@b.pl") == 'from:"a@b.pl" has:attachment filename:pdf'


def test_header_is_case_insensitive_and_missing_returns_none():
    payload = {"headers": [{"name": "From", "value": "x@y.pl"}]}
    assert _header(payload, "from") == "x@y.pl"
    assert _header(payload, "Subject") is None


def test_iter_pdf_parts_finds_nested_pdf_and_ignores_others():
    payload = {
        "parts": [
            {"mimeType": "text/plain", "filename": "", "body": {"data": "x"}},
            {
                "parts": [
                    {
                        "mimeType": "application/pdf",
                        "filename": "faktura.pdf",
                        "body": {"attachmentId": "att1"},
                    }
                ]
            },
        ]
    }
    pdfs = list(_iter_pdf_parts(payload))
    assert len(pdfs) == 1
    assert pdfs[0]["filename"] == "faktura.pdf"


def test_iter_pdf_parts_matches_pdf_by_filename_even_if_mime_octet():
    payload = {
        "parts": [
            {
                "mimeType": "application/octet-stream",
                "filename": "skan.PDF",
                "body": {"attachmentId": "a"},
            }
        ]
    }
    assert len(list(_iter_pdf_parts(payload))) == 1


def test_build_query_quotes_sender_with_spaces():
    assert (
        _build_query("Vendor X <v@x.pl>") == 'from:"Vendor X <v@x.pl>" has:attachment filename:pdf'
    )


def test_b64url_decode_roundtrips_and_handles_empty():
    raw = b"%PDF-1.4 dane"
    encoded = base64.urlsafe_b64encode(raw).decode().rstrip("=")
    assert _b64url_decode(encoded) == raw
    assert _b64url_decode("") == b""


def test_iter_pdf_parts_yields_nothing_for_non_pdf_leaf_or_empty_parts():
    assert list(_iter_pdf_parts({"mimeType": "text/plain", "filename": "x.txt"})) == []
    assert list(_iter_pdf_parts({"parts": []})) == []
