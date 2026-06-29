from __future__ import annotations

import os
from collections.abc import Callable
from datetime import UTC, datetime

from invoicer.booking import BookingPayload, BookingResult
from invoicer.security import redact_pii

# import_uslug (art. 28b) — odwrotne obciazenie (art. 17), flaga reverse_charge wlasciwa.
# WNT (art. 9/100) ma osobne traktowanie w rejestrze Fakturowni — NIE ta flaga; do potwierdzenia
# live przed ewentualnym dodaniem. Konserwatywnie poza zbiorem.
_REVERSE_CHARGE_TREATMENTS = {"import_uslug"}


class FakturowniaError(RuntimeError):
    """Blad integracji z Fakturownia (status != 2xx). Komunikat ma PII zredagowane."""


def _today_iso() -> str:
    return datetime.now(UTC).date().isoformat()


def _positions(payload: BookingPayload) -> list[dict]:
    # Fakturownia wymaga brutto pozycji (total_price_gross) — bez niego POST -> 422.
    # Netto liczy sama z brutto+VAT (brak ryzyka niespojnosci netto/brutto).
    # Kwoty jako string (jak LedgerEntry.total_gross) — stabilny zapis, bez float w kwotach.
    return [
        {
            "name": line.description,
            "quantity": str(line.quantity),
            "total_price_gross": str(line.gross),
            "tax": int(line.vat_rate * 100),
        }
        for line in payload.lines
    ]


class FakturowniaSink:
    """AccountingSink ksiegujacy otrzymana fakture jako koszt (income=0) w Fakturowni.

    `client` jest wstrzykiwany (CI: fake; live: httpx.Client) i musi miec
    `post(url, json) -> resp` z `resp.status_code`, `resp.json()`, `resp.text`.
    `api_token` laduje w body (zgodnie z API), nie w logach.
    """

    sink_name = "fakturownia"

    def __init__(
        self,
        client,
        *,
        domain: str,
        api_token: str,
        clock: Callable[[], str] = _today_iso,
    ) -> None:
        self._client = client
        self._domain = domain
        self._api_token = api_token
        self._clock = clock

    def post(self, payload: BookingPayload) -> BookingResult:
        issue_date = payload.issue_date.isoformat() if payload.issue_date else self._clock()
        # Faktura kosztowa (income=0): Fakturownia renderuje pola seller_* w sekcji "Nabywca",
        # a buyer_* w sekcji "Sprzedawca". Zeby na wydruku role byly poprawne, MY (odbiorca,
        # payload.buyer) idziemy w seller_*, a dostawca z faktury (payload.seller) w buyer_*.
        recipient = payload.buyer  # nasza firma -> sekcja Nabywca
        supplier = payload.seller  # dostawca z PDF -> sekcja Sprzedawca
        invoice = {
            "kind": "vat",
            "income": 0,
            # Numer z oryginalnej faktury (PDF). Bez niego Fakturownia nie nadaje numeru
            # fakturze kosztowej i UI pokazuje pusty numer ("- - -").
            "number": payload.number,
            "issue_date": issue_date,
            "sell_date": issue_date,
            "seller_name": recipient.name,
            "seller_tax_no": recipient.nip or recipient.vat_id,
            "seller_country": recipient.country,
            "buyer_name": supplier.name,
            "buyer_tax_no": supplier.nip or supplier.vat_id,
            "buyer_country": supplier.country,
            "currency": payload.currency,
            "lang": "pl",
            "reverse_charge": payload.treatment in _REVERSE_CHARGE_TREATMENTS,
            "positions": _positions(payload),
        }
        # Termin platnosci bierzemy z faktury (payment_to). Gdy go nie podamy, Fakturownia
        # wyliczy go sama z issue_date + domyslny termin konta — co dawalo bledna date.
        if payload.due_date:
            invoice["payment_to"] = payload.due_date.isoformat()
        body = {"api_token": self._api_token, "invoice": invoice}
        url = f"https://{self._domain}.fakturownia.pl/invoices.json"
        resp = self._client.post(url, json=body)
        if not 200 <= resp.status_code < 300:
            # redaguj PRZED obcieciem — brak fragmentow PII przy granicy 500 znakow
            snippet = redact_pii(str(resp.text))[:500]
            raise FakturowniaError(f"Fakturownia POST {url} -> {resp.status_code}: {snippet}")
        data = resp.json()
        booking_id = data.get("number") or data.get("id")
        if not booking_id:
            raise FakturowniaError(
                f"Fakturownia zwrocilo 2xx bez number/id w odpowiedzi: {list(data.keys())}"
            )
        return BookingResult(booking_id=str(booking_id), sink=self.sink_name)


def build_fakturownia_sink() -> FakturowniaSink:
    """Buduje FakturowniaSink z konfiguracji env (FAKTUROWNIA_API_TOKEN, FAKTUROWNIA_DOMAIN).

    Realny klient httpx; uzywane przez test live i ewentualne reczne wpiecie. KeyError gdy brak env.
    """
    import httpx

    return FakturowniaSink(
        httpx.Client(timeout=30.0),
        domain=os.environ["FAKTUROWNIA_DOMAIN"],
        api_token=os.environ["FAKTUROWNIA_API_TOKEN"],
    )
