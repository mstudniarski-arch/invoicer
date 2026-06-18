from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from invoicer.models import InvoiceDocument


class FixtureSource:
    """EmailSource oparty o lokalny katalog fixture'ow (testy i demo offline).

    Dla pliku `<name>.pdf` WYMAGANY jest sidecar `<name>.json`:
    {"sender": "...", "subject": "...", "received_at": "2026-06-01T10:00:00"}.
    Brak sidecara = blad autorski — metoda _load rzuca FileNotFoundError.
    Pola sender i subject sa opcjonalne wewnatrz sidecara; received_at jest WYMAGANE.
    """

    def __init__(self, directory: Path) -> None:
        self.directory = directory

    def _load(self) -> list[InvoiceDocument]:
        if not self.directory.is_dir():
            raise NotADirectoryError(f"Katalog fixture'ow nie istnieje: {self.directory}")
        docs: list[InvoiceDocument] = []
        for pdf in sorted(self.directory.glob("*.pdf")):
            meta_path = pdf.with_suffix(".json")
            if not meta_path.exists():
                raise FileNotFoundError(
                    f"Brak sidecara metadanych '{meta_path.name}' dla fixture '{pdf.name}'"
                )
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            docs.append(
                InvoiceDocument(
                    sender=meta.get("sender", ""),
                    subject=meta.get("subject", ""),
                    received_at=datetime.fromisoformat(meta["received_at"]),
                    filename=pdf.name,
                    content=pdf.read_bytes(),
                )
            )
        return docs

    def fetch(self, sender: str) -> list[InvoiceDocument]:
        return [doc for doc in self._load() if doc.sender == sender]
