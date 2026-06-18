from __future__ import annotations

from decimal import Decimal

from invoicer.models import Invoice

NIP_WEIGHTS = (6, 5, 7, 2, 3, 4, 5, 6, 7)


def _digits_only(value: str) -> str:
    return "".join(ch for ch in value if ch.isdigit())


def nip_checksum_valid(nip: str | None) -> bool:
    """Walidacja polskiego NIP algorytmem wagowym (mod 11).

    Suma kontrolna == 10 oznacza NIP niepoprawny (cyfra kontrolna nie moze byc 10).
    """
    if not nip:
        return False
    digits = _digits_only(nip)
    if len(digits) != 10:
        return False
    weighted = sum(int(digits[i]) * NIP_WEIGHTS[i] for i in range(9))
    control = weighted % 11
    if control == 10:
        return False
    return control == int(digits[9])


_CENT = Decimal("0.01")


def totals_consistent(invoice: Invoice) -> bool:
    """Sprawdza netto+VAT=brutto globalnie oraz zgodnosc sum pozycji z naglowkiem.

    Tolerancja groszowa na zaokraglenia.
    """
    sum_net = sum((line.net for line in invoice.lines), Decimal("0"))
    sum_vat = sum((line.vat for line in invoice.lines), Decimal("0"))
    sum_gross = sum((line.gross for line in invoice.lines), Decimal("0"))
    return (
        abs(sum_net - invoice.total_net) <= _CENT
        and abs(sum_vat - invoice.total_vat) <= _CENT
        and abs(sum_gross - invoice.total_gross) <= _CENT
        and abs((invoice.total_net + invoice.total_vat) - invoice.total_gross) <= _CENT
    )
