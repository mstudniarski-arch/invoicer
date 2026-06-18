from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from invoicer.models import InvoiceDocument


class FixtureSource:
    """EmailSource oparty o lokalny katalog fixture'ow (testy i demo offline).

    Dla pliku `<name>.pdf` oczekuje sidecara `<name>.json`:
    {"sender": "...", "subject": "...", "received_at": "2026-06-01T10:00:00"}.
    """

    def __init__(self, directory: Path) -> None:
        self.directory = directory

    def _load(self) -> list[InvoiceDocument]:
        docs: list[InvoiceDocument] = []
        for pdf in sorted(self.directory.glob("*.pdf")):
            meta_path = pdf.with_suffix(".json")
            meta = json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.exists() else {}
            docs.append(
                InvoiceDocument(
                    sender=meta.get("sender", ""),
                    subject=meta.get("subject", ""),
                    received_at=datetime.fromisoformat(
                        meta.get("received_at", "1970-01-01T00:00:00")
                    ),
                    filename=pdf.name,
                    content=pdf.read_bytes(),
                )
            )
        return docs

    def fetch(self, sender: str) -> list[InvoiceDocument]:
        return [doc for doc in self._load() if doc.sender == sender]
