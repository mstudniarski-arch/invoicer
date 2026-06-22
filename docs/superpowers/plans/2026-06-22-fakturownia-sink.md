# Invoicer — Plan: realny AccountingSink (FakturowniaSink) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Dodać realny, wywoływalny `AccountingSink` — `FakturowniaSink` — który księguje otrzymaną fakturę jako fakturę kosztową (`income:0`) przez REST API Fakturowni, z deterministycznym testem CI (wstrzykiwany fake klient) i testem live-gated.

**Architecture:** Adapter `FakturowniaSink` w stylu `GmailAdapter`: wstrzykiwany klient HTTP (CI: fake; live: `httpx.Client`). Mapuje `BookingPayload` → JSON faktury kosztowej, `POST /invoices.json`, parsuje wynik na `BookingResult`. Błędy (status ≠ 2xx) → `FakturowniaError` z body przepuszczonym przez `redact_pii` (brak PII w wyjątku/logu). `BookingPayload` zyskuje opcjonalne `issue_date`. Graf/CLI/demo nietknięte — sink wstrzykiwany jak każdy `AccountingSink`; demo zostaje na mocku.

**Tech Stack:** Python 3.12, uv, `httpx`, Pydantic v2, pytest, ruff. Fakturownia REST API (`POST /invoices.json`, auth `api_token` w body, odpowiedź 201 `{id, number}`).

**Spec:** `docs/superpowers/specs/2026-06-22-fakturownia-sink-design.md`.

**Stan wyjściowy:** `main` po Planach 01–08 + redakcja PII. Port `AccountingSink.post(payload: BookingPayload) -> BookingResult` (`src/invoicer/ports.py`). `BookingPayload(seller, buyer, number, currency, lines, total_net, total_vat, total_gross, treatment=None)` i `invoice_to_booking_payload(invoice, treatment=None)` w `src/invoicer/booking.py`. `Party(name, nip=None, country="PL", address=None, vat_id=None)`, `LineItem(description, quantity, unit_net, vat_rate, net, vat, gross)`, `TaxTreatment` = KRAJOWA/IMPORT_USLUG/IMPORT_TOWAROW/WNT/INNE (`src/invoicer/models.py`). `redact_pii` w `src/invoicer/security.py`. Wzorzec live-gated: `tests/live/test_gmail_live.py`. **Gałąź `feat/fakturownia-sink` utworzona; spec scommitowany.** `httpx 0.28.1` dostępny tranzytywnie. Baseline: 148 passed, 3 skipped, ruff czysty. Komendy `uv run`. Importy na górze. `[tool.uv] package=false` (testy importują przez `pythonpath=src`).

---

## File Structure

| Plik | Odpowiedzialność |
|------|------------------|
| `pyproject.toml` (MOD) | + `httpx` w `[project.dependencies]` (jawna zależność). |
| `.env.example` (MOD) | + `FAKTUROWNIA_API_TOKEN`, `FAKTUROWNIA_DOMAIN`. |
| `src/invoicer/booking.py` (MOD) | `BookingPayload` + `issue_date: date | None = None`; mapper przenosi datę. |
| `src/invoicer/adapters/fakturownia.py` (NEW) | `FakturowniaSink`, `FakturowniaError`, `build_fakturownia_sink`. |
| `tests/unit/test_booking.py` (MOD) | + test przenoszenia `issue_date`. |
| `tests/unit/test_fakturownia.py` (NEW) | mapowanie/POST/parsowanie/błąd+redakcja/Protocol (fake client). |
| `tests/live/test_fakturownia_live.py` (NEW) | live-gated: realna faktura kosztowa. |
| `README.md` (MOD) | wiersz `AccountingSink` → `FakturowniaSink`. |

---

## Task 0: Gałąź + httpx + env + baseline

- [ ] **Step 1** — Gałąź `feat/fakturownia-sink` już utworzona. Potwierdź: `cd /Users/mski/Developer/Invoicer && git branch --show-current` → `feat/fakturownia-sink`.
- [ ] **Step 2: Baseline** — `uv run pytest -q` → oczekiwane `148 passed, 3 skipped`. `uv run ruff check .` → clean.
- [ ] **Step 3: httpx jako jawna zależność** — `uv add httpx`. Expected: dodaje `httpx` do `[project.dependencies]`, aktualizuje `uv.lock` (httpx był już tranzytywnie — bez nowego pobrania).
- [ ] **Step 4: `.env.example`** — dopisz dwie linie na końcu pliku:
```
FAKTUROWNIA_API_TOKEN=
FAKTUROWNIA_DOMAIN=
```
- [ ] **Step 5: Sanity + commit** — `uv run python -c "import httpx; print('ok')"` → `ok`. `uv run pytest -q` → 148+3. `uv run ruff check .` → clean.
```bash
git add pyproject.toml uv.lock .env.example
git commit -m "build: add httpx dependency + FAKTUROWNIA_* env template"
```

---

## Task 1: `BookingPayload.issue_date` + mapper

**Files:**
- Modify: `src/invoicer/booking.py`
- Test: `tests/unit/test_booking.py`

- [ ] **Step 1: Add failing test** — APPEND do `tests/unit/test_booking.py` (plik ma już helper `_invoice()` z `issue_date=date(2026, 6, 1)` oraz importy `date`, `invoice_to_booking_payload`):
```python
def test_mapper_carries_issue_date():
    payload = invoice_to_booking_payload(_invoice())
    assert payload.issue_date == date(2026, 6, 1)
```

- [ ] **Step 2: Run to verify it fails** — `uv run pytest tests/unit/test_booking.py::test_mapper_carries_issue_date -v` → FAIL (`AttributeError: 'BookingPayload' object has no attribute 'issue_date'`).

- [ ] **Step 3: Implement** — w `src/invoicer/booking.py`:
  (a) Dodaj import daty na górze (po `from decimal import Decimal`): `from datetime import date`.
  (b) W `class BookingPayload` dodaj pole PO `treatment`:
```python
    issue_date: date | None = None  # data wystawienia faktury (dla realnego sinka)
```
  (c) W `invoice_to_booking_payload(...)` dodaj do konstrukcji `BookingPayload(...)` argument:
```python
        issue_date=invoice.issue_date,
```

- [ ] **Step 4: Verify pass** — `uv run pytest tests/unit/test_booking.py -v` → PASS (istniejące + nowy). `uv run pytest -q` → green (149 passed, 3 skipped). `uv run ruff check . && uv run ruff format --check .` → clean.

- [ ] **Step 5: Commit**
```bash
git add src/invoicer/booking.py tests/unit/test_booking.py
git commit -m "feat: BookingPayload.issue_date (carried by invoice_to_booking_payload)"
```

---

## Task 2: `FakturowniaSink` (mapowanie + POST + błędy)

**Files:**
- Create: `src/invoicer/adapters/fakturownia.py`
- Test: `tests/unit/test_fakturownia.py`

- [ ] **Step 1: Write the failing tests** — utwórz `tests/unit/test_fakturownia.py`:
```python
from datetime import date
from decimal import Decimal

import pytest

from invoicer.adapters.fakturownia import FakturowniaError, FakturowniaSink
from invoicer.booking import BookingPayload
from invoicer.models import LineItem, Party
from invoicer.ports import AccountingSink


class _FakeResponse:
    def __init__(self, status_code: int, json_data: dict | None = None, text: str = "") -> None:
        self.status_code = status_code
        self._json = json_data or {}
        self.text = text

    def json(self) -> dict:
        return self._json


class _FakeClient:
    """Rejestruje wywolania i zwraca skonfigurowana odpowiedz (bez sieci)."""

    def __init__(self, response: _FakeResponse) -> None:
        self._response = response
        self.calls: list[tuple[str, dict]] = []

    def post(self, url: str, json: dict) -> _FakeResponse:
        self.calls.append((url, json))
        return self._response


def _payload(*, treatment: str | None = None) -> BookingPayload:
    line = LineItem(
        description="Usluga",
        quantity=Decimal("2"),
        unit_net=Decimal("1000.00"),
        vat_rate=Decimal("0.23"),
        net=Decimal("2000.00"),
        vat=Decimal("460.00"),
        gross=Decimal("2460.00"),
    )
    return BookingPayload(
        seller=Party(name="Dostawca", nip="5260001246", country="PL"),
        buyer=Party(name="My", nip="1234567890", country="PL"),
        number="FZ/1",
        currency="PLN",
        lines=[line],
        total_net=Decimal("2000.00"),
        total_vat=Decimal("460.00"),
        total_gross=Decimal("2460.00"),
        treatment=treatment,
        issue_date=date(2026, 6, 1),
    )


def _sink(response: _FakeResponse) -> tuple[FakturowniaSink, _FakeClient]:
    client = _FakeClient(response)
    return FakturowniaSink(client, domain="acme", api_token="TKN"), client


def test_post_builds_cost_invoice_and_returns_booking_result():
    sink, client = _sink(_FakeResponse(201, {"id": 9, "number": "FZ/2026/1"}))
    result = sink.post(_payload())
    url, body = client.calls[0]
    assert url == "https://acme.fakturownia.pl/invoices.json"
    assert body["api_token"] == "TKN"
    inv = body["invoice"]
    assert inv["income"] == 0
    assert inv["kind"] == "vat"
    assert inv["issue_date"] == "2026-06-01"
    assert inv["seller_name"] == "Dostawca"
    assert inv["seller_tax_no"] == "5260001246"
    assert inv["buyer_name"] == "My"
    assert inv["currency"] == "PLN"
    assert inv["positions"] == [
        {"name": "Usluga", "quantity": "2", "price_net": "1000.00", "tax": 23}
    ]
    assert result.booking_id == "FZ/2026/1"
    assert result.sink == "fakturownia"
    assert result.status == "posted"


def test_post_falls_back_to_id_when_no_number():
    sink, _ = _sink(_FakeResponse(201, {"id": 7}))
    assert sink.post(_payload()).booking_id == "7"


def test_post_raises_and_redacts_pii_on_error():
    body_text = "blad: NIP 5260001246 nieprawidlowy, kontakt ksiegowa@firma.pl"
    sink, _ = _sink(_FakeResponse(422, text=body_text))
    with pytest.raises(FakturowniaError) as exc:
        sink.post(_payload())
    msg = str(exc.value)
    assert "422" in msg
    assert "5260001246" not in msg
    assert "ksiegowa@firma.pl" not in msg
    assert "[NIP]" in msg
    assert "[EMAIL]" in msg


def test_reverse_charge_set_for_import_uslug_not_for_krajowa():
    sink, client = _sink(_FakeResponse(201, {"id": 1, "number": "X"}))
    sink.post(_payload(treatment="import_uslug"))
    assert client.calls[0][1]["invoice"]["reverse_charge"] is True

    sink2, client2 = _sink(_FakeResponse(201, {"id": 2, "number": "Y"}))
    sink2.post(_payload(treatment="krajowa"))
    assert client2.calls[0][1]["invoice"]["reverse_charge"] is False


def test_conforms_to_accounting_sink_protocol():
    sink, _ = _sink(_FakeResponse(201, {"id": 1}))
    assert isinstance(sink, AccountingSink)
```

- [ ] **Step 2: Run to verify it fails** — `uv run pytest tests/unit/test_fakturownia.py -v` → FAIL (`ModuleNotFoundError: No module named 'invoicer.adapters.fakturownia'`).

- [ ] **Step 3: Implement** — utwórz `src/invoicer/adapters/fakturownia.py` z DOKŁADNIE:
```python
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
```
(Uwaga: `_REVERSE_CHARGE_TREATMENTS` celowo obejmuje `import_uslug` i `wnt` — klarowne przypadki odwrotnego obciążenia PL; `import_towarow`/`inne` poza MVP.)

- [ ] **Step 4: Verify pass** — `uv run pytest tests/unit/test_fakturownia.py -v` → PASS (5). `uv run pytest -q` → green (154 passed, 3 skipped — 149 + 5). `uv run ruff check . && uv run ruff format --check .` → clean.

- [ ] **Step 5: Commit**
```bash
git add src/invoicer/adapters/fakturownia.py tests/unit/test_fakturownia.py
git commit -m "feat: FakturowniaSink (cost invoice via REST, injectable client, PII-redacted errors)"
```

---

## Task 3: Live-gated test (realna faktura kosztowa)

**Files:**
- Create: `tests/live/test_fakturownia_live.py`

> Test pomijany bez `FAKTUROWNIA_API_TOKEN` + `FAKTUROWNIA_DOMAIN` (wzorzec jak `test_gmail_live.py`). Tworzy REALNĄ fakturę kosztową — używać konta testowego Fakturowni.

- [ ] **Step 1: Write the live test** — utwórz `tests/live/test_fakturownia_live.py`:
```python
import os
from datetime import date
from decimal import Decimal

import pytest

from invoicer.adapters.fakturownia import build_fakturownia_sink
from invoicer.booking import BookingPayload
from invoicer.models import LineItem, Party

pytestmark = pytest.mark.skipif(
    not (os.getenv("FAKTUROWNIA_API_TOKEN") and os.getenv("FAKTUROWNIA_DOMAIN")),
    reason="wymaga FAKTUROWNIA_API_TOKEN + FAKTUROWNIA_DOMAIN (test live)",
)


def test_creates_cost_invoice_live():
    line = LineItem(
        description="Usluga testowa (live)",
        quantity=Decimal("1"),
        unit_net=Decimal("100.00"),
        vat_rate=Decimal("0.23"),
        net=Decimal("100.00"),
        vat=Decimal("23.00"),
        gross=Decimal("123.00"),
    )
    payload = BookingPayload(
        seller=Party(name="Dostawca Test", nip="5260001246", country="PL"),
        buyer=Party(name="Nabywca Test", nip="1234567890", country="PL"),
        number="FZ/LIVE/1",
        currency="PLN",
        lines=[line],
        total_net=Decimal("100.00"),
        total_vat=Decimal("23.00"),
        total_gross=Decimal("123.00"),
        treatment="krajowa",
        issue_date=date(2026, 6, 1),
    )
    result = build_fakturownia_sink().post(payload)
    assert result.booking_id  # niepuste — Fakturownia nadala numer/id
    assert result.sink == "fakturownia"
```

- [ ] **Step 2: Verify it skips (bez creds)** — `uv run pytest tests/live/test_fakturownia_live.py -v` → `1 skipped` (brak env). `uv run pytest -q` → 154 passed, **4 skipped** (było 3 + 1 nowy live).

- [ ] **Step 3: (Opcjonalnie, lokalnie) uruchom na żywo** — z `FAKTUROWNIA_API_TOKEN` + `FAKTUROWNIA_DOMAIN` (konto testowe): `uv run pytest tests/live/test_fakturownia_live.py -v` → `1 passed` (utworzono realną fakturę kosztową). To NIE jest częścią CI; odnotuj wynik.

- [ ] **Step 4: Commit**
```bash
git add tests/live/test_fakturownia_live.py
git commit -m "test: live-gated FakturowniaSink (creates real cost invoice when creds set)"
```

---

## Task 4: README + lint + pełny suite

**Files:**
- Modify: `README.md`

- [ ] **Step 1: README — wiersz AccountingSink** — w tabeli portów zmień wiersz `AccountingSink`. Z:
```
| `AccountingSink` | `MockSubiektSink` | `SubiektSferaSink` *(Windows/COM, planned)* |
```
na:
```
| `AccountingSink` | `MockSubiektSink` (offline/demo) | **`FakturowniaSink`** ✅ (REST, faktura kosztowa) |
```
(Pełne odświeżenie README — roadmapa, licznik testów, wiersze Gmail/Streamlit — to osobne zadanie; tu zmieniamy tylko wiersz dotyczący tej pracy.)

- [ ] **Step 2: Lint + pełny suite** — `cd /Users/mski/Developer/Invoicer && uv run ruff check . && uv run ruff format --check .` → clean. `uv run pytest -q` → **154 passed, 4 skipped** (zweryfikuj realne liczby: baseline 148 + 1 booking + 5 fakturownia = 154; skipped 3 + 1 live = 4).

- [ ] **Step 3: Commit**
```bash
git add README.md
git commit -m "docs: README — AccountingSink now FakturowniaSink (real REST adapter)"
```

---

## Self-Review (wykonane przy pisaniu planu)

**Spec coverage:**
- `FakturowniaSink` (mapowanie kosztowa income=0, POST, parsowanie) — spec §3.2 → Task 2 ✓
- Wstrzykiwany klient (CI fake / live httpx) — spec §3.2 → Task 2 (fake) + Task 3 (httpx) ✓
- `BookingPayload.issue_date` — spec §3.1 → Task 1 ✓
- Factory `build_fakturownia_sink` + config env — spec §3.3 → Task 2 (factory) + Task 0 (.env) ✓
- Redakcja PII w błędach — spec §3.2 → Task 2 (`test_post_raises_and_redacts_pii_on_error`) ✓
- Live-gated test — spec §5 → Task 3 ✓
- `httpx` jawne / `.env.example` / README — spec §3.4 → Task 0 + Task 4 ✓
- Reverse charge dla import usług — spec §7 → Task 2 (`test_reverse_charge_*`) ✓
- Demo na mocku, real Subiekt poza zakresem — spec §2 → nietykane (brak zmian w grafie/demie) ✓

**Placeholder scan:** brak TBD/TODO; pełny kod + komendy.

**Type consistency:** `FakturowniaSink(client, *, domain: str, api_token: str, clock=_today_iso)`; `post(payload: BookingPayload) -> BookingResult`; `build_fakturownia_sink() -> FakturowniaSink`; `FakturowniaError(RuntimeError)`. `BookingPayload.issue_date: date | None`. Pola mapowane zgodne z `Party`(`name`/`nip`/`vat_id`/`country`)/`LineItem`(`description`/`quantity`/`unit_net`/`vat_rate`). `BookingResult(booking_id, sink, status="posted")`. Wartości `TaxTreatment` jako stringi (`"import_uslug"`, `"wnt"`, `"krajowa"`). Sukces = 2xx; booking_id = `number` lub `str(id)`.

**Uwaga wykonawcza:** kwoty serializowane jako `str(Decimal)` (spójnie z `LedgerEntry.total_gross` — „Decimal jako string"; bez float w kwotach). `api_token` w body, nie w logach; body błędu przepuszczone przez `redact_pii`. Test live tworzy realny rekord — konto testowe. Sukces 2xx (nie tylko 201) dla odporności. `int(vat_rate*100)` daje całkowity procent (0.23→23, 0.00→0).
