from __future__ import annotations

import base64
from collections.abc import Iterator

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
