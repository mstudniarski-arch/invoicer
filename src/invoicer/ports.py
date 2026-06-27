from __future__ import annotations

from typing import Protocol, runtime_checkable

from invoicer.booking import BookingPayload, BookingResult
from invoicer.models import Classification, Invoice, InvoiceDocument
from invoicer.rag.models import RetrievedChunk


@runtime_checkable
class EmailSource(Protocol):
    """Zrodlo dokumentow: pobiera zalaczniki-faktury od konkretnego nadawcy."""

    def fetch(self, sender: str) -> list[InvoiceDocument]: ...


@runtime_checkable
class AccountingSink(Protocol):
    """Ujscie ksiegowe: przyjmuje gotowy dekret i zwraca wynik zaksiegowania."""

    def post(self, payload: BookingPayload) -> BookingResult: ...


@runtime_checkable
class ApprovalChannel(Protocol):
    """Kanal akceptacji: wysyla do czlowieka request zatwierdzenia faktury."""

    def request_approval(self, payload: dict) -> None: ...


@runtime_checkable
class InvoiceDetector(Protocol):
    """Klasyfikator: czy dokument to faktura (przed wejsciem w pipeline)."""

    def is_invoice(self, document: InvoiceDocument) -> bool: ...


@runtime_checkable
class InvoiceExtractor(Protocol):
    """Wyciaga ustrukturyzowana Invoice z surowego dokumentu (PDF/skan)."""

    def extract(self, document: InvoiceDocument) -> Invoice: ...


@runtime_checkable
class ExceptionReasoner(Protocol):
    """Sedzia-LLM: wzbogaca deterministyczna klasyfikacje faktury zagranicznej."""

    def reason(self, invoice: Invoice, base: Classification) -> Classification: ...


@runtime_checkable
class Embedder(Protocol):
    """Zamienia tekst na wektory. Rozroznia dokument (ingest) i zapytanie (retrieval)."""

    def embed_documents(self, texts: list[str]) -> list[list[float]]: ...

    def embed_query(self, text: str) -> list[float]: ...


@runtime_checkable
class LegalKnowledgeStore(Protocol):
    """Baza wektorowa przepisow: zwraca k najtrafniejszych fragmentow dla zapytania."""

    def search(self, query: str, k: int = 5) -> list[RetrievedChunk]: ...
