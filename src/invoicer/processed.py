from __future__ import annotations

import hashlib
import sqlite3
from datetime import UTC, datetime

from invoicer.models import InvoiceDocument


def document_key(document: InvoiceDocument) -> str:
    """Stabilny klucz deduplikacji dokumentu.

    Gmail: 'message_id:filename' (jeden mail z 2 PDF = 2 rozne klucze).
    Brak message_id (upload/fixtura): 'sha256(content):filename'.
    """
    head = document.message_id or hashlib.sha256(document.content).hexdigest()
    return f"{head}:{document.filename}"


class ProcessedDocuments:
    """Trwaly zbior obsluzonych dokumentow (idempotencja pollingu).

    Status done|failed — OBA znacza 'juz obsluzony, pomijaj' (at-most-once:
    przy bledzie NIE ponawiamy, by nie spamowac prosbami WhatsApp/alertami).
    check_same_thread=False: ten sam plik SQLite co checkpointer/PendingApprovals.
    """

    def __init__(self, db_path: str) -> None:
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.execute(
            "CREATE TABLE IF NOT EXISTS processed_documents ("
            "doc_key TEXT PRIMARY KEY, status TEXT NOT NULL, updated_at TEXT NOT NULL)"
        )
        self._conn.commit()

    def seen(self, doc_key: str) -> bool:
        """True jesli dokument byl juz obsluzony (dowolny status)."""
        row = self._conn.execute(
            "SELECT 1 FROM processed_documents WHERE doc_key = ? LIMIT 1", (doc_key,)
        ).fetchone()
        return row is not None

    def mark(self, doc_key: str, status: str) -> None:
        """Zapisuje dokument jako obsluzony ('done'|'failed'); idempotentne."""
        self._conn.execute(
            "INSERT OR REPLACE INTO processed_documents (doc_key, status, updated_at) "
            "VALUES (?, ?, ?)",
            (doc_key, status, datetime.now(UTC).isoformat()),
        )
        self._conn.commit()
