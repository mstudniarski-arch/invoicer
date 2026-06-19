from __future__ import annotations

from datetime import date
from decimal import Decimal, InvalidOperation

from pydantic import BaseModel, Field

from invoicer.models import Invoice, LineItem, Party


class PartyExtraction(BaseModel):
    name: str = Field(description="Nazwa firmy/strony")
    nip: str | None = Field(default=None, description="NIP (tylko cyfry), jesli jest")
    country: str = Field(default="PL", description="Kod kraju ISO-2, np. PL, GB, DE")
    address: str | None = Field(default=None, description="Adres, jesli jest")
    vat_id: str | None = Field(default=None, description="Numer VAT UE/zagraniczny, jesli jest")


class LineItemExtraction(BaseModel):
    description: str
    quantity: str = Field(description="Ilosc jako liczba dziesietna w tekscie, np. '1' lub '2.5'")
    unit_net: str = Field(description="Cena jednostkowa netto, tekst, np. '1000.00'")
    vat_rate: str = Field(description="Stawka VAT jako ulamek dziesietny w tekscie, np. '0.23'")
    net: str = Field(description="Wartosc netto pozycji, tekst, np. '1000.00'")
    vat: str = Field(description="Kwota VAT pozycji, tekst, np. '230.00'")
    gross: str = Field(description="Wartosc brutto pozycji, tekst, np. '1230.00'")


class InvoiceExtraction(BaseModel):
    """DTO wypelniane przez LLM (with_structured_output). Kwoty jako tekst dziesietny."""

    seller: PartyExtraction
    buyer: PartyExtraction
    number: str
    issue_date: str = Field(description="Data wystawienia w formacie ISO RRRR-MM-DD")
    sale_date: str | None = Field(default=None, description="Data sprzedazy ISO, jesli jest")
    due_date: str | None = Field(default=None, description="Termin platnosci ISO, jesli jest")
    currency: str = Field(default="PLN", description="Kod waluty, np. PLN, GBP, EUR")
    lines: list[LineItemExtraction]
    total_net: str
    total_vat: str
    total_gross: str
    confidence: float = Field(
        default=1.0, ge=0.0, le=1.0, description="Pewnosc ekstrakcji 0..1; obniz dla slabego skanu"
    )


def _amount(field: str, raw: str) -> Decimal:
    try:
        return Decimal(raw)
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f"Niepoprawna kwota w polu '{field}': {raw!r}") from exc


def _iso_date(field: str, raw: str) -> date:
    try:
        return date.fromisoformat(raw)
    except ValueError as exc:
        raise ValueError(f"Niepoprawna data w polu '{field}': {raw!r}") from exc


def _party(p: PartyExtraction) -> Party:
    return Party(name=p.name, nip=p.nip, country=p.country, address=p.address, vat_id=p.vat_id)


def _line(line: LineItemExtraction) -> LineItem:
    return LineItem(
        description=line.description,
        quantity=_amount("quantity", line.quantity),
        unit_net=_amount("unit_net", line.unit_net),
        vat_rate=_amount("vat_rate", line.vat_rate),
        net=_amount("net", line.net),
        vat=_amount("vat", line.vat),
        gross=_amount("gross", line.gross),
    )


def extraction_to_invoice(ex: InvoiceExtraction) -> Invoice:
    """Czysta konwersja DTO LLM -> domenowy Invoice (kwoty Decimal, daty date)."""
    return Invoice(
        seller=_party(ex.seller),
        buyer=_party(ex.buyer),
        number=ex.number,
        issue_date=_iso_date("issue_date", ex.issue_date),
        sale_date=_iso_date("sale_date", ex.sale_date) if ex.sale_date else None,
        due_date=_iso_date("due_date", ex.due_date) if ex.due_date else None,
        currency=ex.currency,
        lines=[_line(line) for line in ex.lines],
        total_net=_amount("total_net", ex.total_net),
        total_vat=_amount("total_vat", ex.total_vat),
        total_gross=_amount("total_gross", ex.total_gross),
        extraction_confidence=ex.confidence,
    )
