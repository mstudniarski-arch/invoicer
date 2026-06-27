from __future__ import annotations

from invoicer.models import Invoice


def build_retrieval_query(invoice: Invoice) -> str:
    """Tekst zapytania do retrievalu/reasonera — TYLKO pola z allowlisty (spec §9).

    Kraj sprzedawcy, obecnosc VAT, waluta, kwoty zbiorcze, opisy pozycji.
    BEZ PII nabywcy, BEZ adresow, BEZ nazw stron — dziedziczy gwarancje prywatnosci reasonera.
    """
    lines = "; ".join(f"{ln.description} (netto {ln.net})" for ln in invoice.lines)
    return (
        f"Kraj sprzedawcy: {invoice.seller.country}\n"
        f"VAT na fakturze: {'tak' if invoice.total_vat > 0 else 'brak'}\n"
        f"Waluta: {invoice.currency}\n"
        f"Suma netto: {invoice.total_net}; suma brutto: {invoice.total_gross}\n"
        f"Pozycje: {lines}"
    )
