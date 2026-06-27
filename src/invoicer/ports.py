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
    """Sedzia-LLM: gruntuje klasyfikacje faktury zagranicznej w dostarczonym kontekscie prawnym."""

    def reason(
        self,
        invoice: Invoice,
        base: Classification,
        context: list[RetrievedChunk] | None = None,
    ) -> Classification: ...


@runtime_checkable
class Embedder(Protocol):
    """Zamienia tekst na wektory. Rozroznia dokument (ingest) i zapytanie (retrieval)."""

    def embed_documents(self, texts: list[str]) -> list[list[float]]: ...

    def embed_query(self, text: str) -> list[float]: ...


@runtime_checkable
class LegalKnowledgeStore(Protocol):
    """Baza wektorowa przepisow: zwraca k najtrafniejszych fragmentow dla zapytania."""

    def search(self, query: str, k: int = 5) -> list[RetrievedChunk]: ...


@runtime_checkable
class Reranker(Protocol):
    """Przeszereguj dokumenty wzgledem zapytania. Zwraca (indeks_oryginalny, score) malejaco."""

    def rerank(self, query: str, documents: list[str], top_k: int) -> list[tuple[int, float]]: ...
