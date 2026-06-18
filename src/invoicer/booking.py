from __future__ import annotations

from decimal import Decimal

from pydantic import BaseModel

from invoicer.models import Invoice, LineItem, Party


class BookingPayload(BaseModel):
    """Znormalizowany dekret przekazywany do AccountingSink (mock Subiekt / realny Sfera)."""

    seller: Party
    buyer: Party
    number: str
    currency: str
    lines: list[LineItem]
    total_net: Decimal
    total_vat: Decimal
    total_gross: Decimal
    treatment: str | None = None  # traktowanie podatkowe — uzupelnia klasyfikacja (Plan 04)


class BookingResult(BaseModel):
    booking_id: str
    sink: str
    status: str = "posted"


def invoice_to_booking_payload(invoice: Invoice, treatment: str | None = None) -> BookingPayload:
    """Mapuje zwalidowana fakture na dekret dla programu ksiegowego."""
    return BookingPayload(
        seller=invoice.seller,
        buyer=invoice.buyer,
        number=invoice.number,
        currency=invoice.currency,
        lines=invoice.lines,
        total_net=invoice.total_net,
        total_vat=invoice.total_vat,
        total_gross=invoice.total_gross,
        treatment=treatment,
    )
