from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel


class LedgerEntry(BaseModel):
    number: str
    seller_name: str
    total_gross: str  # Decimal jako string — stabilny zapis JSON
    booking_id: str
    booked_at: str  # ISO-8601, ustawiane przez wolajacego (determinizm)
    seller_nip: str | None = None


def _dedup_key(number: str, seller_nip: str | None, seller_name: str) -> tuple[str, str]:
    return (number, seller_nip or seller_name)


class Ledger:
    """Append-only rejestr zaksiegowanych faktur (JSONL) z wykrywaniem duplikatow.

    Klucz duplikatu: (numer, NIP sprzedawcy) albo (numer, nazwa) gdy brak NIP.
    """

    def __init__(self, path: Path) -> None:
        self.path = path

    def append(self, entry: LedgerEntry) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as f:
            f.write(entry.model_dump_json() + "\n")

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
