from __future__ import annotations

import hashlib
import json
from pathlib import Path

from pydantic import BaseModel


class LedgerEntry(BaseModel):
    number: str
    seller_name: str
    total_gross: str  # Decimal jako string — stabilny zapis JSON
    booking_id: str
    booked_at: str  # ISO-8601, ustawiane przez wolajacego (determinizm)
    seller_nip: str | None = None
    prev_hash: str = ""  # entry_hash poprzedniego wpisu (lancuch audytu)
    entry_hash: str = ""  # SHA-256 tresci tego wpisu (z prev_hash)


def _dedup_key(number: str, seller_nip: str | None, seller_name: str) -> tuple[str, str]:
    return (number, seller_nip or seller_name)


def _entry_hash(entry: LedgerEntry) -> str:
    content = json.dumps(
        [
            entry.number,
            entry.seller_nip,
            entry.seller_name,
            entry.total_gross,
            entry.booking_id,
            entry.booked_at,
            entry.prev_hash,
        ],
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


class Ledger:
    """Append-only rejestr zaksiegowanych faktur (JSONL) z wykrywaniem duplikatow.

    Klucz duplikatu: (numer, NIP sprzedawcy) albo (numer, nazwa) gdy brak NIP.
    """

    def __init__(self, path: Path) -> None:
        self.path = path

    def append(self, entry: LedgerEntry) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        existing = self.entries()
        prev_hash = existing[-1].entry_hash if existing else ""
        stamped = entry.model_copy(update={"prev_hash": prev_hash})
        stamped = stamped.model_copy(update={"entry_hash": _entry_hash(stamped)})
        with self.path.open("a", encoding="utf-8") as f:
            f.write(stamped.model_dump_json() + "\n")

    def entries(self) -> list[LedgerEntry]:
        if not self.path.exists():
            return []
        return [
            LedgerEntry.model_validate_json(line)
            for line in self.path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]

    def is_duplicate(self, number: str, seller_nip: str | None, seller_name: str) -> bool:
        key = _dedup_key(number, seller_nip, seller_name)
        return any(_dedup_key(e.number, e.seller_nip, e.seller_name) == key for e in self.entries())

    def verify_chain(self) -> bool:
        """Sprawdza integralnosc lancucha (wykrywa manipulacje pliku)."""
        prev = ""
        for entry in self.entries():
            if entry.prev_hash != prev or entry.entry_hash != _entry_hash(entry):
                return False
            prev = entry.entry_hash
        return True
