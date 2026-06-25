import base64
from datetime import date

from invoicer.adapters.gmail import (
    GMAIL_SCOPES,
    GmailAdapter,
    _b64url_decode,
    _build_query,
    _header,
    _iter_pdf_parts,
)
from invoicer.models import InvoiceDocument
from invoicer.ports import EmailSource


def test_scope_is_readonly():
    assert GMAIL_SCOPES == ["https://www.googleapis.com/auth/gmail.readonly"]


def test_build_query_filters_unread_pdf_today_only():
    q = _build_query("a@b.pl", today=date(2026, 6, 25))
    assert (
        q == "from:a@b.pl after:2026/06/25 before:2026/06/26 "
        "has:attachment filename:pdf is:unread"
    )


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
    q = _build_query("Vendor X <v@x.pl>", today=date(2026, 6, 25))
    assert (
        q == 'from:"Vendor X <v@x.pl>" after:2026/06/25 before:2026/06/26 '
        "has:attachment filename:pdf is:unread"
    )


def test_b64url_decode_roundtrips_and_handles_empty():
    raw = b"%PDF-1.4 dane"
    encoded = base64.urlsafe_b64encode(raw).decode().rstrip("=")
    assert _b64url_decode(encoded) == raw
    assert _b64url_decode("") == b""


def test_iter_pdf_parts_yields_nothing_for_non_pdf_leaf_or_empty_parts():
    assert list(_iter_pdf_parts({"mimeType": "text/plain", "filename": "x.txt"})) == []
    assert list(_iter_pdf_parts({"parts": []})) == []


def _message_fixture() -> dict:
    return {
        "id": "m1",
        "internalDate": "1780272000000",
        "payload": {
            "headers": [
                {"name": "From", "value": "Ksiegowa <ksiegowa@klient.pl>"},
                {"name": "Subject", "value": "Faktura FV/1"},
            ],
            "parts": [
                {"mimeType": "text/plain", "filename": "", "body": {"data": "aGVq"}},
                {
                    "mimeType": "application/pdf",
                    "filename": "faktura.pdf",
                    "body": {"attachmentId": "att1"},
                },
            ],
        },
    }


class _Exec:
    def __init__(self, result):
        self._result = result

    def execute(self):
        return self._result


class _Attachments:
    def __init__(self, data):
        self._data = data

    def get(self, **_kwargs):
        return _Exec({"data": self._data})


class _Messages:
    def __init__(self, list_result, get_result, attach_data):
        self._list_result = list_result
        self._get_result = get_result
        self._attach_data = attach_data

    def list(self, **_kwargs):
        return _Exec(self._list_result)

    def get(self, **_kwargs):
        return _Exec(self._get_result)

    def attachments(self):
        return _Attachments(self._attach_data)


class _Users:
    def __init__(self, messages):
        self._messages = messages

    def messages(self):
        return self._messages


class _FakeGmail:
    def __init__(self, *, list_result, get_result, attach_data):
        self._users = _Users(_Messages(list_result, get_result, attach_data))

    def users(self):
        return self._users


def test_gmail_adapter_satisfies_email_source_protocol():
    service = _FakeGmail(list_result={}, get_result=None, attach_data="")
    assert isinstance(GmailAdapter(service), EmailSource)


def test_fetch_builds_invoice_document_from_pdf_attachment():
    pdf = b"%PDF-1.4 dane"
    b64 = base64.urlsafe_b64encode(pdf).decode().rstrip("=")
    service = _FakeGmail(
        list_result={"messages": [{"id": "m1"}]},
        get_result=_message_fixture(),
        attach_data=b64,
    )
    docs = GmailAdapter(service).fetch("ksiegowa@klient.pl")
    assert len(docs) == 1
    doc = docs[0]
    assert isinstance(doc, InvoiceDocument)
    assert doc.content == pdf
    assert doc.filename == "faktura.pdf"
    assert "ksiegowa@klient.pl" in doc.sender
    assert doc.subject == "Faktura FV/1"
    assert doc.message_id == "m1"  # message_id z ref["id"] (klucz dedup)


def test_fetch_returns_empty_when_no_messages():
    service = _FakeGmail(list_result={}, get_result=None, attach_data="")
    assert GmailAdapter(service).fetch("nikt@x.pl") == []


class _PaginatedMessages:
    def __init__(self, pages, get_result, attach_data):
        self._pages = pages  # dict: pageToken (or None) -> list_result
        self._get_result = get_result
        self._attach_data = attach_data

    def list(self, *, pageToken=None, **_kwargs):
        return _Exec(self._pages[pageToken])

    def get(self, **_kwargs):
        return _Exec(self._get_result)

    def attachments(self):
        return _Attachments(self._attach_data)


class _PaginatedGmail:
    def __init__(self, pages, get_result, attach_data):
        self._users = _Users(_PaginatedMessages(pages, get_result, attach_data))

    def users(self):
        return self._users


def test_fetch_follows_pagination_across_pages():
    pdf = b"%PDF-1.4 strona"
    b64 = base64.urlsafe_b64encode(pdf).decode().rstrip("=")
    pages = {
        None: {"messages": [{"id": "m1"}], "nextPageToken": "p2"},
        "p2": {"messages": [{"id": "m2"}]},
    }
    service = _PaginatedGmail(pages, _message_fixture(), b64)
    docs = GmailAdapter(service).fetch("x@y.pl")
    assert len(docs) == 2  # po jednej fakturze z kazdej strony


def test_fetch_uses_inline_attachment_data_when_present():
    pdf = b"%PDF-inline"
    b64 = base64.urlsafe_b64encode(pdf).decode().rstrip("=")
    msg = {
        "id": "m1",
        "internalDate": "1780272000000",
        "payload": {
            "headers": [{"name": "From", "value": "a@b.pl"}],
            "parts": [{"mimeType": "application/pdf", "filename": "x.pdf", "body": {"data": b64}}],
        },
    }
    service = _FakeGmail(
        list_result={"messages": [{"id": "m1"}]}, get_result=msg, attach_data="NIEUZYWANE"
    )
    docs = GmailAdapter(service).fetch("a@b.pl")
    assert docs[0].content == pdf  # uzyto body.data, nie attachments()


def test_fetch_skips_message_without_payload():
    service = _FakeGmail(
        list_result={"messages": [{"id": "m1"}]}, get_result={"id": "m1"}, attach_data=""
    )
    assert GmailAdapter(service).fetch("a@b.pl") == []  # brak payload -> pominiete, bez wyjatku


class _CapturingMessages:
    def __init__(self):
        self.queries = []

    def list(self, **kwargs):
        self.queries.append(kwargs.get("q"))
        return _Exec({})

    def get(self, **_kwargs):
        return _Exec(None)

    def attachments(self):
        return _Attachments("")


class _CapturingGmail:
    def __init__(self):
        self.msgs = _CapturingMessages()
        self._users = _Users(self.msgs)

    def users(self):
        return self._users


def test_fetch_query_filters_today_and_unread():
    service = _CapturingGmail()
    GmailAdapter(service).fetch("a@b.pl", today=date(2026, 6, 25))
    assert service.msgs.queries[0] == (
        "from:a@b.pl after:2026/06/25 before:2026/06/26 has:attachment filename:pdf is:unread"
    )
