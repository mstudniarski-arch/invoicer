# Invoicer — Design: realny AccountingSink przez Fakturownia REST API

**Data:** 2026-06-22
**Status:** zatwierdzony projekt (do realizacji subagent-driven, jak Plany 01–08)
**Realizuje:** „realny adapter księgowy" z roadmapy — zamiast niedostępnego `SubiektSferaSink` (Windows/COM) integrujemy z realnym, wywoływalnym REST API polskiego SaaS księgowego (Fakturownia).

---

## 1. Problem / kontekst

Port `AccountingSink` (`src/invoicer/ports.py`) ma dziś tylko `MockSubiektSink`. Realny `SubiektSferaSink` wymaga Windows + Sfera (COM) — **niedostępny** (brak Subiekta/Windows). Aby projekt miał prawdziwą, **budowalną i testowalną** integrację księgową, wybrano **Fakturownię** — polski SaaS z publicznym REST API (free/sandbox), wywoływalnym i live-testowalnym.

**Semantyka:** projekt księguje **otrzymaną** fakturę zakupu. Fakturownia rozróżnia faktury przychodowe (`income: 1`) i **kosztowe** (`income: 0`). Otrzymaną fakturę zakupu mapujemy na **fakturę kosztową** (`income: 0`): `seller` = dostawca (z faktury), `buyer` = my (nabywca).

**Fakty API (zweryfikowane, github.com/fakturownia/API + context7):**
- `POST https://{DOMENA}.fakturownia.pl/invoices.json`, `Content-Type: application/json`.
- Auth: pole `api_token` w body JSON.
- Body: `{ "api_token": "...", "invoice": { kind, income, issue_date, sell_date, payment_to|payment_to_kind, seller_name, seller_tax_no, seller_country, buyer_name, buyer_tax_no, buyer_country, currency, lang, reverse_charge, positions: [...] } }`.
- `positions[]`: `{ name, quantity, tax (procent int lub "np"/"zw"), price_net | total_price_gross, ... }`.
- Odpowiedź **201** z JSON `{ id (int), number (string), kind, ... }`. Błąd → kod ≠ 2xx + body z opisem.

**Wzorce w repo do naśladowania:** `GmailAdapter` (wstrzykiwany klient → CI fake / live realny; live-gated test). `build_invoice_graph(..., clock=...)` (wstrzykiwany zegar dla determinizmu). `redact_pii` (maskowanie PII — użyte w komunikatach błędów).

---

## 2. Zakres

**W zakresie:**
- `FakturowniaSink` (AccountingSink) — mapuje `BookingPayload` → faktura kosztowa, POST, parsuje wynik.
- Wstrzykiwany klient HTTP (CI: fake; live: `httpx.Client`); deterministyczne testy jednostkowe.
- `BookingPayload.issue_date` (opcjonalne, backward-compat) — sink wysyła realną datę faktury.
- Factory `build_fakturownia_sink()` z konfiguracją z env; live-gated test.
- Redakcja PII w komunikatach błędów (synergia z mechanizmem redakcji logów).
- `httpx` jako jawna zależność; `.env.example` + FAKTUROWNIA_*; README: wiersz `AccountingSink` → `FakturowniaSink`.

**Poza zakresem (świadome YAGNI / osobne):**
- **Wpięcie w demo** — Streamlit/`build_demo_graph` zostają na `MockSubiektSink` (decyzja: zero przypadkowych realnych zapisów przy demie). FakturowniaSink udowodniony live-testem; ręczne wpięcie udokumentowane.
- Pełne pokrycie API Fakturowni (klienci/produkty/magazyn, korekty, załączniki, KSeF) — tylko tworzenie faktury kosztowej.
- Retry/backoff/rate-limiting ponad pojedyncze, jasne zgłoszenie błędu (MVP).
- Realny `SubiektSferaSink` (Windows/COM) — pozostaje świadomie odłożony; `MockSubiektSink` zostaje dla demo/CI.

---

## 3. Architektura

### 3.1 `BookingPayload` (MOD `src/invoicer/booking.py`)
Dodać opcjonalne `issue_date: date | None = None` (po `treatment`). `invoice_to_booking_payload` wypełnia je z `invoice.issue_date`. Backward-compatible (istniejące konstrukcje bez `issue_date` → `None`).

### 3.2 `FakturowniaSink` (NEW `src/invoicer/adapters/fakturownia.py`)
```
class FakturowniaSink:  # spełnia AccountingSink (Protocol)
    sink_name = "fakturownia"
    def __init__(self, client, *, domain: str, api_token: str, clock: Callable[[], str] = <dzis ISO>): ...
    def post(self, payload: BookingPayload) -> BookingResult: ...
```
- `client` — wstrzyknięty poster (httpx.Client-like: `.post(url, json=...) -> resp`; `resp.status_code`, `resp.json()`). CI: fake; live: realny `httpx.Client`.
- `post`:
  1. Buduje body: `api_token`, `invoice` z `income=0`, `kind="vat"`, `issue_date`/`sell_date` = `payload.issue_date` (lub `clock()` gdy None), `seller_*` = `payload.seller` (dostawca), `buyer_*` = `payload.buyer`, `currency`, `positions` z `payload.lines` (`name`=description, `quantity`, `tax`=int(vat_rate*100), `price_net`=unit_net). `reverse_charge=True` gdy `payload.treatment` wskazuje odwrotne obciążenie/import usług (mapowanie traktowań → flaga).
  2. `POST https://{domain}.fakturownia.pl/invoices.json`.
  3. Status 201 → `data = resp.json()`; `BookingResult(booking_id=str(data.get("number") or data["id"]), sink="fakturownia", status="posted")`.
  4. Status ≠ 2xx → `raise FakturowniaError(f"...{status}...{redact_pii(body_snippet)}")` — **PII w błędzie zredagowane**.

### 3.3 Factory + config
`build_fakturownia_sink() -> FakturowniaSink`: czyta `FAKTUROWNIA_API_TOKEN` + `FAKTUROWNIA_DOMAIN` z env, tworzy `httpx.Client()` (timeout), zwraca skonfigurowany sink. Używane przez live-test (i ręczne wpięcie). Brak env → jasny błąd / pomijane przez live-gating.

### 3.4 Zależności / config / docs
- `httpx` → `[project.dependencies]` (już obecne tranzytywnie; jawne, by nie polegać na tranzytywności).
- `.env.example`: `FAKTUROWNIA_API_TOKEN=`, `FAKTUROWNIA_DOMAIN=`.
- README: tabela portów — `AccountingSink | MockSubiektSink (offline) | FakturowniaSink ✅` (zamiast `SubiektSferaSink (planned, COM)`).

---

## 4. Przepływ danych

```
BookingPayload (dekret: seller=dostawca, buyer=my, lines, totals, issue_date, treatment)
   │  FakturowniaSink.post()
   ▼
{ api_token, invoice: { income:0, kind:"vat", seller_*, buyer_*, positions[], currency, issue_date } }
   │  client.post(https://{domain}.fakturownia.pl/invoices.json)
   ▼
201 { id, number }  ──►  BookingResult(booking_id=number, sink="fakturownia")
≠2xx                ──►  FakturowniaError(redact_pii(body))   # brak PII w wyjatku/logu
```
Graf/CLI/demo nietknięte — sink wstrzykiwany jak każdy `AccountingSink` (`build_invoice_graph(sink=...)`). Demo zostaje na mocku.

---

## 5. Testy

**`tests/unit/test_fakturownia.py` (NEW, deterministyczne, fake client):**
- `post` buduje poprawne body: URL = `https://{domain}.fakturownia.pl/invoices.json`, `api_token` obecny, `invoice.income == 0`, `kind == "vat"`, `positions` zmapowane z `lines` (name/quantity/tax/price_net), seller=dostawca, buyer=my, `issue_date` z payloadu.
- Odpowiedź 201 `{id, number}` → `BookingResult(booking_id=number, sink="fakturownia", status="posted")`.
- Status ≠ 2xx → podnosi `FakturowniaError`; komunikat **nie zawiera** surowego PII (np. NIP/email z body są zredagowane).
- Zgodność z portem: `isinstance(FakturowniaSink(fake, domain="x", api_token="t"), AccountingSink)`.
- `reverse_charge` ustawiane dla traktowania „import usług".
- Fake client: rejestruje `(url, json)` i zwraca skonfigurowaną odpowiedź (status + json) — bez sieci.

**`tests/live/test_fakturownia_live.py` (NEW, live-gated):**
- `pytestmark = skipif(not (FAKTUROWNIA_API_TOKEN and FAKTUROWNIA_DOMAIN))`.
- `build_fakturownia_sink().post(<payload kosztowy>)` → realna faktura kosztowa; asercja niepustego `booking_id`. (Używać konta testowego Fakturowni.)

**`tests/unit/test_booking.py` (MOD):** `invoice_to_booking_payload` przenosi `issue_date` z faktury; istniejące testy zielone (pole opcjonalne).

---

## 6. Podział na taski (subagent-driven)

- **Task 0:** gałąź `feat/fakturownia-sink` (utworzona), `uv add httpx`, `.env.example` + FAKTUROWNIA_*, baseline (`uv run pytest -q` → 148+3).
- **Task 1:** `BookingPayload.issue_date` + `invoice_to_booking_payload` + testy (TDD).
- **Task 2:** `FakturowniaSink` (mapowanie + post + `FakturowniaError` z redakcją) + `tests/unit/test_fakturownia.py` (fake client) (TDD).
- **Task 3:** `build_fakturownia_sink()` (env) + `tests/live/test_fakturownia_live.py` (live-gated).
- **Task 4:** README (wiersz AccountingSink) + lint + pełny suite (zielona baza).
- **Finał:** review opus + merge `--no-ff` do `main`.

---

## 7. Ryzyka / decyzje

- **Cost vs income:** otrzymana faktura → `income: 0` (kosztowa). Zweryfikowane w API.
- **`issue_date` w BookingPayload:** opcjonalne (backward-compat); realny przepływ wypełnia z faktury; fallback `clock()` (wstrzykiwany, determinizm w testach).
- **Wstrzykiwany klient HTTP:** CI deterministyczne (fake), live realny `httpx.Client` — wzorzec z `GmailAdapter`. `api_token` w body (zgodnie z API), nie w logach.
- **Redakcja błędów:** body odpowiedzi błędu przepuszczone przez `redact_pii` przed włożeniem do wyjątku/logu — spójne z §9.
- **Mapowanie `tax`/reverse-charge:** `tax = int(vat_rate*100)`; dla traktowań odwrotnego obciążenia/importu usług `reverse_charge=True` (stawka 0/„np"). Pełna macierz stawek PL — poza MVP (mapujemy realne przypadki z grafu).
- **booking_id:** `number` Fakturowni (fallback `str(id)`) — spójne ze stylem `MockSubiektSink` („MOCK-{number}").
- **Demo na mocku:** świadomie (zero realnych zapisów przy demie); FakturowniaSink dowodzony live-testem.
