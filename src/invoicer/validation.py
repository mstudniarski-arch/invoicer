from __future__ import annotations

from decimal import Decimal

from invoicer.ledger import Ledger
from invoicer.models import Check, CheckStatus, Invoice, ValidationResult

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
    """Sprawdza netto+VAT=brutto globalnie, zgodnosc sum pozycji z naglowkiem oraz per-pozycja.

    Tolerancja groszowa na zaokraglenia. Kazda pozycja musi spelniac net+vat=gross
    (spec §6), zeby bledy per-pozycja nie mogly sie kasowac w sumie globalnej.
    """
    sum_net = sum((line.net for line in invoice.lines), Decimal("0"))
    sum_vat = sum((line.vat for line in invoice.lines), Decimal("0"))
    sum_gross = sum((line.gross for line in invoice.lines), Decimal("0"))
    return (
        abs(sum_net - invoice.total_net) <= _CENT
        and abs(sum_vat - invoice.total_vat) <= _CENT
        and abs(sum_gross - invoice.total_gross) <= _CENT
        and abs((invoice.total_net + invoice.total_vat) - invoice.total_gross) <= _CENT
        and all(abs((line.net + line.vat) - line.gross) <= _CENT for line in invoice.lines)
    )


def validate_invoice(invoice: Invoice, ledger: Ledger | None = None) -> ValidationResult:
    """Łączy kontrole deterministyczne w jeden ValidationResult.

    NIP wymagany tylko dla sprzedawcy z PL; zagraniczny → WARN (nie FAIL).
    Jesli ledger jest podany, sprawdza duplikaty (numer + NIP/nazwa sprzedawcy).
    Duplikat ustawia check "duplicate" na FAIL i is_duplicate=True, blokujac ksiegowanie.
    Bez ledgera duplikaty nie sa sprawdzane (zachowanie z Planu 01).
    """
    checks: list[Check] = []

    if invoice.seller.country == "PL":
        if nip_checksum_valid(invoice.seller.nip):
            checks.append(Check(name="nip", status=CheckStatus.PASS))
        else:
            checks.append(
                Check(
                    name="nip",
                    status=CheckStatus.FAIL,
                    detail="Niepoprawny NIP sprzedawcy (suma kontrolna)",
                )
            )
    else:
        checks.append(
            Check(
                name="nip",
                status=CheckStatus.WARN,
                detail="Sprzedawca zagraniczny — NIP PL nie dotyczy",
            )
        )

    if totals_consistent(invoice):
        checks.append(Check(name="sums", status=CheckStatus.PASS))
    else:
        checks.append(
            Check(
                name="sums",
                status=CheckStatus.FAIL,
                detail="Niespojne sumy (netto+VAT≠brutto lub Σ pozycji)",
            )
        )

    if invoice.lines:
        checks.append(Check(name="lines", status=CheckStatus.PASS))
    else:
        checks.append(Check(name="lines", status=CheckStatus.FAIL, detail="Brak pozycji"))

    is_duplicate = False
    if ledger is not None:
        is_duplicate = ledger.is_duplicate(invoice.number, invoice.seller.nip, invoice.seller.name)
        if is_duplicate:
            checks.append(
                Check(
                    name="duplicate",
                    status=CheckStatus.FAIL,
                    detail="Faktura juz zaksiegowana (numer + sprzedawca)",
                )
            )
        else:
            checks.append(Check(name="duplicate", status=CheckStatus.PASS))

    return ValidationResult(checks=checks, is_duplicate=is_duplicate)
