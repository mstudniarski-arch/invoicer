from __future__ import annotations

from typing import Protocol, runtime_checkable

from invoicer.booking import BookingPayload, BookingResult
from invoicer.models import Invoice, InvoiceDocument


@runtime_checkable
class EmailSource(Protocol):
    """Zrodlo dokumentow: pobiera zalaczniki-faktury od konkretnego nadawcy."""

    def fetch(self, sender: str) -> list[InvoiceDocument]: ...


@runtime_checkable
class AccountingSink(Protocol):
    """Ujscie ksiegowe: przyjmuje gotowy dekret i zwraca wynik zaksiegowania."""

    def post(self, payload: BookingPayload) -> BookingResult: ...


@runtime_checkable
class InvoiceExtractor(Protocol):
    """Wyciaga ustrukturyzowana Invoice z surowego dokumentu (PDF/skan)."""

    def extract(self, document: InvoiceDocument) -> Invoice: ...
