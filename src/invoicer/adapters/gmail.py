from __future__ import annotations

import base64
from collections.abc import Iterator
from datetime import UTC, datetime

from invoicer.models import InvoiceDocument

GMAIL_SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]


def _build_query(sender: str) -> str:
    """Zapytanie Gmail: faktury (PDF) od konkretnego nadawcy."""
    return f'from:"{sender}" has:attachment filename:pdf'


def _header(payload: dict, name: str) -> str | None:
    """Wartosc naglowka (case-insensitive) z payloadu wiadomosci."""
    lowered = name.lower()
    for header in payload.get("headers", []):
        if header.get("name", "").lower() == lowered:
            return header.get("value")
    return None


def _iter_pdf_parts(payload: dict) -> Iterator[dict]:
    """Rekurencyjnie wyszukuje czesci bedace zalacznikami PDF (po MIME lub rozszerzeniu)."""
    parts = payload.get("parts")
    if parts:
        for part in parts:
            yield from _iter_pdf_parts(part)
        return
    filename = payload.get("filename", "")
    mime = payload.get("mimeType", "")
    if mime == "application/pdf" or filename.lower().endswith(".pdf"):
        yield payload


def _b64url_decode(data: str) -> bytes:
    """Dekoduje base64url z Gmaila (uzupelnia brakujacy padding)."""
    return base64.urlsafe_b64decode(data + "=" * (-len(data) % 4))


def _attachment_bytes(service, user_id: str, message_id: str, part: dict) -> bytes:
    """Pobiera bajty zalacznika: z body.data jesli inline, inaczej przez attachments().get."""
    body = part.get("body", {})
    data = body.get("data")
    if data is None:
        attachment_id = body["attachmentId"]
        data = (
            service.users()
            .messages()
            .attachments()
            .get(userId=user_id, messageId=message_id, id=attachment_id)
            .execute()["data"]
        )
    return _b64url_decode(data)


class GmailAdapter:
    """EmailSource oparty o Gmail API. `service` jest wstrzykiwany (CI: fake; realny: build()).

    Pobiera wiadomosci od `sender` z zalacznikami PDF i mapuje je na InvoiceDocument.
    """

    def __init__(self, service, *, user_id: str = "me") -> None:
        self._service = service
        self._user_id = user_id

    def fetch(self, sender: str) -> list[InvoiceDocument]:
        messages = self._service.users().messages()
        query = _build_query(sender)
        docs: list[InvoiceDocument] = []
        page_token: str | None = None
        while True:
            kwargs = {"userId": self._user_id, "q": query}
            if page_token:
                kwargs["pageToken"] = page_token
            listing = messages.list(**kwargs).execute()
            for ref in listing.get("messages", []):
                msg = messages.get(userId=self._user_id, id=ref["id"], format="full").execute()
                payload = msg.get("payload")
                if payload is None:  # niekompletna/tombstone wiadomosc — pomijamy, nie wywalamy
                    continue
                from_header = _header(payload, "From") or sender
                subject = _header(payload, "Subject") or ""
                ts = int(msg.get("internalDate", "0")) / 1000
                received_at = datetime.fromtimestamp(ts, tz=UTC)
                for part in _iter_pdf_parts(payload):
                    content = _attachment_bytes(self._service, self._user_id, ref["id"], part)
                    docs.append(
                        InvoiceDocument(
                            sender=from_header,
                            subject=subject,
                            received_at=received_at,
                            filename=part.get("filename", "attachment.pdf"),
                            content=content,
                        )
                    )
            page_token = listing.get("nextPageToken")
            if not page_token:
                break
        return docs
