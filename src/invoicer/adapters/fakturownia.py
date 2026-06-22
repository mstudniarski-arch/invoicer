from __future__ import annotations

import os
from collections.abc import Callable
from datetime import UTC, datetime

from invoicer.booking import BookingPayload, BookingResult
from invoicer.security import redact_pii

_REVERSE_CHARGE_TREATMENTS = {"import_uslug", "wnt"}


class FakturowniaError(RuntimeError):
    """Blad integracji z Fakturownia (status != 2xx). Komunikat ma PII zredagowane."""


def _today_iso() -> str:
    return datetime.now(UTC).date().isoformat()


def _positions(payload: BookingPayload) -> list[dict]:
    # kwoty jako string (jak LedgerEntry.total_gross) — stabilny zapis, bez float w kwotach
    return [
        {
            "name": line.description,
            "quantity": str(line.quantity),
            "price_net": str(line.unit_net),
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
        body = {
            "api_token": self._api_token,
            "invoice": {
                "kind": "vat",
                "income": 0,
                "issue_date": issue_date,
                "sell_date": issue_date,
                "seller_name": payload.seller.name,
                "seller_tax_no": payload.seller.nip or payload.seller.vat_id,
                "seller_country": payload.seller.country,
                "buyer_name": payload.buyer.name,
                "buyer_tax_no": payload.buyer.nip or payload.buyer.vat_id,
                "buyer_country": payload.buyer.country,
                "currency": payload.currency,
                "lang": "pl",
                "reverse_charge": payload.treatment in _REVERSE_CHARGE_TREATMENTS,
                "positions": _positions(payload),
            },
        }
        url = f"https://{self._domain}.fakturownia.pl/invoices.json"
        resp = self._client.post(url, json=body)
        if not 200 <= resp.status_code < 300:
            snippet = redact_pii(str(resp.text)[:500])
            raise FakturowniaError(f"Fakturownia POST {url} -> {resp.status_code}: {snippet}")
        data = resp.json()
        booking_id = str(data.get("number") or data["id"])
        return BookingResult(booking_id=booking_id, sink=self.sink_name)


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
