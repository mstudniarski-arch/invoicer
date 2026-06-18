from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum

from pydantic import BaseModel


class Party(BaseModel):
    name: str
    nip: str | None = None
    country: str = "PL"  # ISO-2
    address: str | None = None
    vat_id: str | None = None


class LineItem(BaseModel):
    description: str
    quantity: Decimal
    unit_net: Decimal
    vat_rate: Decimal  # np. Decimal("0.23")
    # net/vat/gross: wartosci wyekstrahowane z faktury (nie wyliczamy) — spojnosc sprawdza walidacja
    net: Decimal
    vat: Decimal
    gross: Decimal


class Invoice(BaseModel):
    seller: Party
    buyer: Party
    number: str
    issue_date: date
    sale_date: date | None = None
    due_date: date | None = None
    currency: str = "PLN"
    lines: list[LineItem]
    total_net: Decimal
    total_vat: Decimal
    total_gross: Decimal
    extraction_confidence: float | None = None


class InvoiceDocument(BaseModel):
    """Surowy dokument wejsciowy (zalacznik e-mail) zanim nastapi ekstrakcja."""

    sender: str
    received_at: datetime
    filename: str
    content: bytes
    subject: str = ""


class CheckStatus(StrEnum):
    PASS = "pass"
    WARN = "warn"
    FAIL = "fail"


class Check(BaseModel):
    name: str
    status: CheckStatus
    detail: str = ""


class ValidationResult(BaseModel):
    checks: list[Check]
    is_duplicate: bool = False

    @property
    def hard_errors(self) -> list[Check]:
        return [c for c in self.checks if c.status == CheckStatus.FAIL]

    @property
    def soft_flags(self) -> list[Check]:
        return [c for c in self.checks if c.status == CheckStatus.WARN]

    @property
    def ok(self) -> bool:
        return not self.hard_errors
