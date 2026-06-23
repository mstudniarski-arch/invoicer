from __future__ import annotations

import sqlite3


class PendingApprovals:
    """Trwaly rejestr oczekujacych akceptacji: numer telefonu -> thread_id (FIFO via rowid).

    Mapuje przychodzaca odpowiedz WhatsApp (po numerze nadawcy) na thread do wznowienia.
    check_same_thread=False: webhook (inny watek/proces) korzysta z tego samego pliku.
    """

    def __init__(self, db_path: str) -> None:
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.execute(
            "CREATE TABLE IF NOT EXISTS pending_approvals ("
            "thread_id TEXT NOT NULL, sender_phone TEXT NOT NULL, "
            "status TEXT NOT NULL DEFAULT 'pending')"
        )
        self._conn.commit()

    def add(self, thread_id: str, phone: str) -> None:
        self._conn.execute(
            "INSERT INTO pending_approvals (thread_id, sender_phone, status) "
            "VALUES (?, ?, 'pending')",
            (thread_id, phone),
        )
        self._conn.commit()

    def resolve_oldest(self, phone: str) -> str | None:
        """Zwraca thread_id najstarszego PENDING dla numeru i oznacza go RESOLVED (FIFO)."""
        row = self._conn.execute(
            "SELECT rowid, thread_id FROM pending_approvals "
            "WHERE sender_phone = ? AND status = 'pending' ORDER BY rowid LIMIT 1",
            (phone,),
        ).fetchone()
        if row is None:
            return None
        rowid, thread_id = row
        self._conn.execute(
            "UPDATE pending_approvals SET status = 'resolved' WHERE rowid = ?", (rowid,)
        )
        self._conn.commit()
        return thread_id
