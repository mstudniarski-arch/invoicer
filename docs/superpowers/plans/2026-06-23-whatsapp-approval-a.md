# Invoicer — Plan A: WhatsApp approval — wychodzące + trwałość Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Umożliwić wysyłanie requestu akceptacji faktury na WhatsApp (sprzedawca / NIP / kwota) przez Twilio, oraz uczynić pauzę grafu trwałą (`SqliteSaver`), by approve mógł nadejść później/z innego procesu.

**Architecture:** Trwały checkpointer `SqliteSaver` wstrzykiwany do `build_invoice_graph(checkpointer=...)` (helper `persistent_checkpointer`). Port `ApprovalChannel` z adapterem `TwilioWhatsAppChannel` (wstrzykiwany klient HTTP, REST Twilio, basic auth) i `StubApprovalChannel` (CI). Payload `human_review` zyskuje `seller_nip`. To Plan A z 2 (Plan B = webhook + resume-on-reply).

**Tech Stack:** Python 3.12, uv, `langgraph-checkpoint-sqlite` (`SqliteSaver`), `httpx` (Twilio REST), Pydantic v2, pytest, ruff.

**Spec:** `docs/superpowers/specs/2026-06-23-whatsapp-approval-design.md`.

**Stan wyjściowy:** `main` + sesja. `build_invoice_graph(*, extractor, ledger, sink, reasoner=None, clock=None, checkpointer=None)` (domyślnie `InMemorySaver`). `human_review` payload w `src/invoicer/graph/nodes.py` (number/seller/country/total_gross/currency/validation_ok/flags/treatment/rationale/must_confirm — **bez NIP**). `runner.py`: `start_document`/`resume_document` + helpery. `ports.py`: porty `@runtime_checkable` (`from typing import Protocol, runtime_checkable`, importuje `InvoiceDocument`). Stub-adaptery to zwykłe klasy. `redact_pii` w `security.py`. `tests/unit/test_runner.py` ma helpery `_invoice()` (seller nip `"5260001246"`, number `"FV/1"`), `_doc()`, `_graph(tmp_path)`. Wzorzec REST+fake-client: `FakturowniaSink`/`tests/unit/test_fakturownia.py`. **Gałąź `feat/whatsapp-approval-a` utworzona; spec scommitowany.** `langgraph-checkpoint-sqlite==3.1.0` już dodany (working tree); `httpx` już jest. **Zweryfikowane API:** `SqliteSaver(sqlite3.connect(path, check_same_thread=False))` + `.setup()`; durable resume działa między instancjami grafu z tej samej bazy. Baseline: 167 passed, 5 skipped, ruff czysty. Komendy `uv run`. `[tool.uv] package=false` (`pythonpath=src`).

---

## File Structure

| Plik | Odpowiedzialność |
|------|------------------|
| `pyproject.toml`/`uv.lock` (MOD) | + `langgraph-checkpoint-sqlite`. |
| `.env.example` (MOD) | + TWILIO_* / APPROVER_WHATSAPP_TO. |
| `src/invoicer/runner.py` (MOD) | `persistent_checkpointer(db_path)`. |
| `src/invoicer/graph/nodes.py` (MOD) | `seller_nip` w payloadzie `human_review`. |
| `src/invoicer/ports.py` (MOD) | port `ApprovalChannel`. |
| `src/invoicer/adapters/stub_approval.py` (NEW) | `StubApprovalChannel`. |
| `src/invoicer/adapters/twilio_whatsapp.py` (NEW) | `TwilioWhatsAppChannel`, `format_approval_message`, `build_twilio_whatsapp_channel`, `TwilioError`. |
| `tests/unit/test_runner.py` (MOD) | durable resume + seller_nip payload. |
| `tests/unit/test_approval.py` (NEW) | stub + Twilio (fake client) + Protocol. |
| `tests/live/test_twilio_whatsapp_live.py` (NEW) | live-gated wysyłka. |

---

## Task 0: Zależność + env + baseline

- [ ] **Step 1** — Gałąź `feat/whatsapp-approval-a` już utworzona. Potwierdź: `cd /Users/mski/Developer/Invoicer && git branch --show-current`.
- [ ] **Step 2: Baseline** — `uv run pytest -q` → `167 passed, 5 skipped`. `uv run ruff check .` → clean.
- [ ] **Step 3: Zależność** — `uv add langgraph-checkpoint-sqlite` (idempotentne — może już być w `pyproject.toml`). Sanity: `uv run python -c "from langgraph.checkpoint.sqlite import SqliteSaver; print('ok')"` → `ok`.
- [ ] **Step 4: `.env.example`** — dopisz na końcu:
```
TWILIO_ACCOUNT_SID=
TWILIO_AUTH_TOKEN=
TWILIO_WHATSAPP_FROM=
APPROVER_WHATSAPP_TO=
```
- [ ] **Step 5: Commit**
```bash
git add pyproject.toml uv.lock .env.example
git commit -m "build: add langgraph-checkpoint-sqlite + TWILIO_* env template"
```

---

## Task 1: Trwały checkpointer (`persistent_checkpointer`)

**Files:**
- Modify: `src/invoicer/runner.py`
- Test: `tests/unit/test_runner.py`

- [ ] **Step 1: Add failing test** — APPEND do `tests/unit/test_runner.py` (helpery `_invoice()`, `_doc()` już istnieją; dodaj brakujące importy w teście tylko jeśli nie ma — `Ledger`, `StubExtractor`, `MockSubiektSink`, `build_invoice_graph` są już importowane na górze pliku z Task 1 P07):
```python
def test_persistent_checkpointer_resumes_across_graph_instances(tmp_path):
    from invoicer.runner import persistent_checkpointer

    db = str(tmp_path / "cp.sqlite")
    ledger_path = tmp_path / "l.jsonl"

    def _make_graph():
        return build_invoice_graph(
            extractor=StubExtractor(_invoice()),
            ledger=Ledger(ledger_path),
            sink=MockSubiektSink(),
            clock=lambda: "2026-06-01T10:00:00",
            checkpointer=persistent_checkpointer(db),
        )

    start_document(_make_graph(), _doc(), thread_id="p1")  # pauza, stan w SQLite
    final = resume_document(_make_graph(), thread_id="p1", decision="approve")  # nowa instancja
    assert final["booking"].booking_id == "MOCK-FV/1"
```

- [ ] **Step 2: Run to verify it fails** — `uv run pytest tests/unit/test_runner.py -k persistent_checkpointer -v` → FAIL (`ImportError: cannot import name 'persistent_checkpointer'`).

- [ ] **Step 3: Implement** — w `src/invoicer/runner.py`:
  (a) Dodaj importy na górze (stdlib `sqlite3` w grupie stdlib; `SqliteSaver` w grupie third-party z innymi `langgraph`):
```python
import sqlite3
```
```python
from langgraph.checkpoint.sqlite import SqliteSaver
```
  (b) APPEND funkcję:
```python
def persistent_checkpointer(db_path: str) -> SqliteSaver:
    """Trwaly checkpointer LangGraph (SQLite) — graf przezywa proces (async approve).

    check_same_thread=False: webhook (inny watek/proces) wznawia ten sam thread_id.
    """
    conn = sqlite3.connect(db_path, check_same_thread=False)
    saver = SqliteSaver(conn)
    saver.setup()
    return saver
```

- [ ] **Step 4: Verify pass** — `uv run pytest tests/unit/test_runner.py -v` → PASS (existing + new). `uv run pytest -q` → green (168 passed, 5 skipped). `uv run ruff check . && uv run ruff format --check .` → clean.

- [ ] **Step 5: Commit**
```bash
git add src/invoicer/runner.py tests/unit/test_runner.py
git commit -m "feat: persistent_checkpointer (SqliteSaver) — durable graph pause/resume across processes"
```

---

## Task 2: `seller_nip` w payloadzie `human_review`

**Files:**
- Modify: `src/invoicer/graph/nodes.py`
- Test: `tests/unit/test_runner.py`

- [ ] **Step 1: Add failing test** — APPEND do `tests/unit/test_runner.py`:
```python
def test_human_review_payload_includes_seller_nip(tmp_path):
    payload = start_document(_graph(tmp_path), _doc(), thread_id="nip1")
    assert payload["seller_nip"] == "5260001246"
```

- [ ] **Step 2: Run to verify it fails** — `uv run pytest tests/unit/test_runner.py -k seller_nip -v` → FAIL (`KeyError: 'seller_nip'`).

- [ ] **Step 3: Implement** — w `src/invoicer/graph/nodes.py`, w funkcji `human_review`, w dict-cie `payload` dodaj klucz `seller_nip` (zaraz po `"seller"`):
```python
        "seller": invoice.seller.name,
        "seller_nip": invoice.seller.nip,
        "country": invoice.seller.country,
```

- [ ] **Step 4: Verify pass** — `uv run pytest tests/unit/test_runner.py -v` → PASS. `uv run pytest -q` → green (169 passed, 5 skipped). `uv run ruff check . && uv run ruff format --check .` → clean. (Streamlit ignoruje nadmiarowy klucz — bez zmian.)

- [ ] **Step 5: Commit**
```bash
git add src/invoicer/graph/nodes.py tests/unit/test_runner.py
git commit -m "feat: include seller_nip in human_review payload (for WhatsApp approval)"
```

---

## Task 3: Port `ApprovalChannel` + `StubApprovalChannel`

**Files:**
- Modify: `src/invoicer/ports.py`
- Create: `src/invoicer/adapters/stub_approval.py`
- Test: `tests/unit/test_approval.py` (NEW)

- [ ] **Step 1: Write failing tests** — utwórz `tests/unit/test_approval.py`:
```python
from invoicer.adapters.stub_approval import StubApprovalChannel
from invoicer.ports import ApprovalChannel

_PAYLOAD = {
    "number": "FV/1",
    "seller": "ACME",
    "seller_nip": "5260001246",
    "total_gross": "1230.00",
    "currency": "PLN",
    "treatment": "krajowa",
}


def test_stub_records_calls():
    ch = StubApprovalChannel()
    ch.request_approval(_PAYLOAD)
    assert ch.sent == [_PAYLOAD]


def test_stub_satisfies_approval_channel_protocol():
    assert isinstance(StubApprovalChannel(), ApprovalChannel)
```

- [ ] **Step 2: Run to verify it fails** — `uv run pytest tests/unit/test_approval.py -v` → FAIL (`ModuleNotFoundError: invoicer.adapters.stub_approval` / `ImportError: ApprovalChannel`).

- [ ] **Step 3: Implement**
  (a) W `src/invoicer/ports.py` dodaj (po `AccountingSink`; `Protocol`/`runtime_checkable` już importowane):
```python
@runtime_checkable
class ApprovalChannel(Protocol):
    """Kanal akceptacji: wysyla do czlowieka request zatwierdzenia faktury."""

    def request_approval(self, payload: dict) -> None: ...
```
  (b) Utwórz `src/invoicer/adapters/stub_approval.py`:
```python
from __future__ import annotations


class StubApprovalChannel:
    """Testowy ApprovalChannel: rejestruje wyslane payloady (CI/offline)."""

    def __init__(self) -> None:
        self.sent: list[dict] = []

    def request_approval(self, payload: dict) -> None:
        self.sent.append(payload)
```

- [ ] **Step 4: Verify pass** — `uv run pytest tests/unit/test_approval.py -v` → PASS (2). `uv run pytest -q` → green (171 passed, 5 skipped). `uv run ruff check . && uv run ruff format --check .` → clean.

- [ ] **Step 5: Commit**
```bash
git add src/invoicer/ports.py src/invoicer/adapters/stub_approval.py tests/unit/test_approval.py
git commit -m "feat: ApprovalChannel port + StubApprovalChannel"
```

> Uwaga projektowa: port to `request_approval(payload)` (bez `thread_id`) — mapowanie odpowiedź→thread robi orkiestrator/rejestr w Planie B (FIFO per numer), więc kanał tego nie potrzebuje (uproszczenie wobec szkicu spec §3.3).

---

## Task 4: `TwilioWhatsAppChannel`

**Files:**
- Create: `src/invoicer/adapters/twilio_whatsapp.py`
- Test: `tests/unit/test_approval.py` (MOD — append)
- Test: `tests/live/test_twilio_whatsapp_live.py` (NEW)

- [ ] **Step 1: Add failing tests** — APPEND do `tests/unit/test_approval.py`:
```python
import pytest


class _FakeResponse:
    def __init__(self, status_code: int, text: str = "") -> None:
        self.status_code = status_code
        self.text = text


class _FakeClient:
    def __init__(self, response: _FakeResponse) -> None:
        self._response = response
        self.calls: list[dict] = []

    def post(self, url, *, data=None, auth=None):
        self.calls.append({"url": url, "data": data, "auth": auth})
        return self._response


def _channel(response):
    from invoicer.adapters.twilio_whatsapp import TwilioWhatsAppChannel

    client = _FakeClient(response)
    ch = TwilioWhatsAppChannel(
        client,
        account_sid="AC123",
        auth_token="tok",
        from_whatsapp="whatsapp:+14155238886",
        to_whatsapp="whatsapp:+48500100200",
    )
    return ch, client


def test_format_message_has_seller_nip_amount():
    from invoicer.adapters.twilio_whatsapp import format_approval_message

    msg = format_approval_message(_PAYLOAD)
    assert "ACME" in msg
    assert "5260001246" in msg
    assert "1230.00 PLN" in msg
    assert "TAK" in msg and "NIE" in msg


def test_request_approval_posts_to_twilio():
    ch, client = _channel(_FakeResponse(201))
    ch.request_approval(_PAYLOAD)
    call = client.calls[0]
    assert call["url"] == "https://api.twilio.com/2010-04-01/Accounts/AC123/Messages.json"
    assert call["auth"] == ("AC123", "tok")
    assert call["data"]["From"] == "whatsapp:+14155238886"
    assert call["data"]["To"] == "whatsapp:+48500100200"
    assert "5260001246" in call["data"]["Body"]


def test_request_approval_raises_and_redacts_on_error():
    from invoicer.adapters.twilio_whatsapp import TwilioError

    ch, _ = _channel(_FakeResponse(401, text="blad: token dla NIP 5260001246, mail x@y.pl"))
    with pytest.raises(TwilioError) as exc:
        ch.request_approval(_PAYLOAD)
    msg = str(exc.value)
    assert "401" in msg
    assert "5260001246" not in msg
    assert "x@y.pl" not in msg


def test_twilio_channel_satisfies_protocol():
    ch, _ = _channel(_FakeResponse(201))
    assert isinstance(ch, ApprovalChannel)
```

- [ ] **Step 2: Run to verify it fails** — `uv run pytest tests/unit/test_approval.py -k "twilio or format_message or request_approval" -v` → FAIL (`ModuleNotFoundError: invoicer.adapters.twilio_whatsapp`).

- [ ] **Step 3: Implement** — utwórz `src/invoicer/adapters/twilio_whatsapp.py`:
```python
from __future__ import annotations

import os

from invoicer.security import redact_pii


class TwilioError(RuntimeError):
    """Blad wysylki przez Twilio (status != 2xx). Komunikat ma PII zredagowane."""


def format_approval_message(payload: dict) -> str:
    """Tresc requestu akceptacji na WhatsApp: sprzedawca, NIP, kwota + instrukcja TAK/NIE."""
    return (
        f"🧾 Faktura {payload['number']}\n"
        f"Od: {payload['seller']}\n"
        f"NIP: {payload.get('seller_nip') or '—'}\n"
        f"Kwota: {payload['total_gross']} {payload['currency']}\n"
        f"Traktowanie: {payload.get('treatment', '—')}\n"
        f"Odpowiedz TAK (zatwierdz) lub NIE (odrzuc)."
    )


class TwilioWhatsAppChannel:
    """ApprovalChannel: wysyla request akceptacji jako wiadomosc WhatsApp przez Twilio REST.

    `client` wstrzykiwany (CI: fake; live: httpx.Client) z `post(url, *, data, auth) -> resp`.
    `auth_token` nigdy nie trafia do logow; bledy idą przez redact_pii.
    """

    def __init__(
        self,
        client,
        *,
        account_sid: str,
        auth_token: str,
        from_whatsapp: str,
        to_whatsapp: str,
    ) -> None:
        self._client = client
        self._sid = account_sid
        self._token = auth_token
        self._from = from_whatsapp
        self._to = to_whatsapp

    def request_approval(self, payload: dict) -> None:
        url = f"https://api.twilio.com/2010-04-01/Accounts/{self._sid}/Messages.json"
        data = {"From": self._from, "To": self._to, "Body": format_approval_message(payload)}
        resp = self._client.post(url, data=data, auth=(self._sid, self._token))
        if not 200 <= resp.status_code < 300:
            snippet = redact_pii(str(resp.text))[:500]
            raise TwilioError(f"Twilio POST {url} -> {resp.status_code}: {snippet}")


def build_twilio_whatsapp_channel() -> TwilioWhatsAppChannel:
    """Buduje kanal z env (TWILIO_ACCOUNT_SID/AUTH_TOKEN/WHATSAPP_FROM, APPROVER_WHATSAPP_TO)."""
    import httpx

    return TwilioWhatsAppChannel(
        httpx.Client(timeout=30.0),
        account_sid=os.environ["TWILIO_ACCOUNT_SID"],
        auth_token=os.environ["TWILIO_AUTH_TOKEN"],
        from_whatsapp=os.environ["TWILIO_WHATSAPP_FROM"],
        to_whatsapp=os.environ["APPROVER_WHATSAPP_TO"],
    )
```

- [ ] **Step 4: Add live-gated test** — utwórz `tests/live/test_twilio_whatsapp_live.py`:
```python
import os

import pytest

from invoicer.adapters.twilio_whatsapp import build_twilio_whatsapp_channel

pytestmark = pytest.mark.skipif(
    not (
        os.getenv("TWILIO_ACCOUNT_SID")
        and os.getenv("TWILIO_AUTH_TOKEN")
        and os.getenv("TWILIO_WHATSAPP_FROM")
        and os.getenv("APPROVER_WHATSAPP_TO")
    ),
    reason="wymaga TWILIO_* + APPROVER_WHATSAPP_TO (test live)",
)


def test_sends_real_whatsapp_approval_request():
    payload = {
        "number": "FV/LIVE/1",
        "seller": "ACME Test",
        "seller_nip": "5260001246",
        "total_gross": "1230.00",
        "currency": "PLN",
        "treatment": "krajowa",
    }
    build_twilio_whatsapp_channel().request_approval(payload)  # brak wyjatku = wyslane
```

- [ ] **Step 5: Verify pass** — `uv run pytest tests/unit/test_approval.py -v` → PASS (2 + 4 = 6). `uv run pytest tests/live/test_twilio_whatsapp_live.py -v` → `1 skipped`. `uv run pytest -q` → green (175 passed, 6 skipped). `uv run ruff check . && uv run ruff format --check .` → clean.

- [ ] **Step 6: Commit**
```bash
git add src/invoicer/adapters/twilio_whatsapp.py tests/unit/test_approval.py tests/live/test_twilio_whatsapp_live.py
git commit -m "feat: TwilioWhatsAppChannel (REST send: seller/NIP/amount, PII-redacted errors) + live test"
```

---

## Task 5: Lint + pełny suite (zielona baza)

- [ ] **Step 1: Ruff** — `cd /Users/mski/Developer/Invoicer && uv run ruff check . && uv run ruff format --check .` → clean.
- [ ] **Step 2: Pełny suite** — `uv run pytest -q` → **175 passed, 6 skipped** (zweryfikuj realne liczby: 167 + 1 durable + 1 nip + 2 stub + 4 twilio = 175; skipped 5 + 1 twilio-live = 6).
- [ ] **Step 3: Commit porządkowy (jeśli ruff coś zmienił)** — `git add -A && git commit -m "chore: ruff clean, green suite (WhatsApp approval Plan A)" || echo "nic do commita"`.

---

## Self-Review (wykonane przy pisaniu planu)

**Spec coverage (Plan A z §6):**
- Durable checkpointer (`SqliteSaver`) — spec §3.1 → Task 1 ✓ (API zweryfikowane empirycznie: durable resume między instancjami)
- `seller_nip` w payloadzie — spec §3.2 → Task 2 ✓
- Port `ApprovalChannel` + `StubApprovalChannel` — spec §3.3 → Task 3 ✓
- `TwilioWhatsAppChannel` (seller/NIP/kwota, błędy redagowane) + factory env — spec §3.3 → Task 4 ✓
- Live-gated Twilio — spec §5 → Task 4 ✓
- Env/dep — spec §6 → Task 0 ✓
- Plan B (webhook/rejestr/orkiestracja) — świadomie POZA tym planem.

**Placeholder scan:** brak TBD/TODO; pełny kod + komendy.

**Type consistency:** `persistent_checkpointer(db_path: str) -> SqliteSaver`; `ApprovalChannel.request_approval(payload: dict) -> None`; `StubApprovalChannel.sent: list[dict]`; `TwilioWhatsAppChannel(client, *, account_sid, auth_token, from_whatsapp, to_whatsapp).request_approval(payload) -> None`; `format_approval_message(payload) -> str`; `build_twilio_whatsapp_channel() -> TwilioWhatsAppChannel`; `TwilioError(RuntimeError)`. Payload zawiera `seller_nip` (Task 2) — `format_approval_message` go czyta. Fake client (`post(url, *, data, auth)`) zgodny z `httpx.Client.post`.

**Uwaga wykonawcza:** port `request_approval(payload)` bez `thread_id` (mapowanie w Planie B). `SqliteSaver` z `check_same_thread=False` (webhook = inny wątek). `auth_token` w `auth=(sid,token)` (basic auth), nie w logach; body błędu przez `redact_pii` (redact-before-truncate). Kwoty/NIP jako string z payloadu. Liczniki testów orientacyjne — zweryfikuj realne.
