from __future__ import annotations

from datetime import date
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
    issue_date: date | None = None  # data wystawienia faktury (dla realnego sinka)


class BookingResult(BaseModel):
    booking_id: str
    sink: str
    status: str = "posted"


def invoice_to_booking_payload(invoice: Invoice, treatment: str | None = None) -> BookingPayload:
    """Mapuje zwalidowana fakture na niezalezny snapshot-dekret dla programu ksiegowego.

    Zagniezdzone modele (seller/buyer/lines) sa kopiowane (deep), wiec pozniejsze
    zmiany faktury nie wplywaja na juz utworzony dekret (ani odwrotnie).
    """
    return BookingPayload(
        seller=invoice.seller.model_copy(deep=True),
        buyer=invoice.buyer.model_copy(deep=True),
        number=invoice.number,
        currency=invoice.currency,
        lines=[line.model_copy(deep=True) for line in invoice.lines],
        total_net=invoice.total_net,
        total_vat=invoice.total_vat,
        total_gross=invoice.total_gross,
        treatment=treatment,
        issue_date=invoice.issue_date,
    )
