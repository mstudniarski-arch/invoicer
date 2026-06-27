from __future__ import annotations

from invoicer.models import Classification, Invoice
from invoicer.rag.models import RetrievedChunk


class IdentityReasoner:
    """Domyslny ExceptionReasoner: zwraca klasyfikacje bez zmian (no-op). Ignoruje kontekst.

    Pozwala uzywac grafu bez realnego LLM (zachowuje deterministyczna klasyfikacje z P03).
    """

    def reason(
        self, invoice: Invoice, base: Classification, context: list[RetrievedChunk] | None = None
    ) -> Classification:
        return base


class StubExceptionReasoner:
    """Testowy ExceptionReasoner: zwraca z gory ustalona klasyfikacje."""

    def __init__(self, classification: Classification) -> None:
        self._classification = classification

    def reason(
        self, invoice: Invoice, base: Classification, context: list[RetrievedChunk] | None = None
    ) -> Classification:
        return self._classification
