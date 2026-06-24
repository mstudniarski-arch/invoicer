# Deploy Runtime 24/7 Implementation Plan (Plan 1 z 2)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Postawic agenta jako jeden zawsze-zywy serwis na Fly.io: realny webhook akceptacji + in-process scheduler codziennego zaciagu z Gmaila + trwala SQLite na wolumenie, z `/health` i `/status`.

**Architecture:** `src/invoicer/app.py` to fabryka FastAPI montujaca logike Planu B webhooka (`create_inbound_app`) na **tej samej** instancji, dodajaca `/health` i `/status`, oraz startujaca scheduler w `lifespan`. Scheduler (APScheduler, AsyncIOScheduler) odpala `run_daily_intake` cron 08:00 Europe/Warsaw. Sekrety przez `fly secrets`; `token.json` (Gmail OAuth) wstrzykiwany jako `GMAIL_TOKEN_B64` i dekodowany do `/data/token.json` przy starcie. Sink przelaczalny przez `INVOICER_SINK` (mock/realny Fakturownia).

**Tech Stack:** Python 3.12, `uv`, `fastapi`/`uvicorn` (juz w deps), `apscheduler` (nowy), `langgraph-checkpoint-sqlite` (juz), Docker, Fly.io (`flyctl`). Observability/CI-CD/Sentry/alerty → Plan 2.

**Spec:** `docs/superpowers/specs/2026-06-24-deployment-observability-design.md`
**Branch:** `feat/deploy-observability` (juz utworzona; spec `4ec26ac`).

---

## File Structure

| Plik | Odpowiedzialnosc | Akcja |
|------|------------------|-------|
| `src/invoicer/approvals.py` | + `count_pending(phone: str \| None = None) -> int` (dla /status). | Modify (Task 1) |
| `src/invoicer/adapters/twilio_whatsapp.py` | + `notify(text: str)` — generyczny POST WhatsApp do alertow (uzywane w Planie 2). | Modify (Task 1B) |
| `src/invoicer/bootstrap.py` | `bootstrap_gmail_token(b64_env, dest)` — dekoduje `GMAIL_TOKEN_B64` -> plik (jesli nie istnieje). Czysta funkcja IO. | Create (Task 2) |
| `src/invoicer/observability_status.py` | `pipeline_status(metrics, counters, registry, *, phone) -> dict` (LLM totals + processed/failed/pending + uptime). Brak PII. | Create (Task 3) |
| `src/invoicer/scheduler.py` | `run_daily_intake(graph, channel, registry, source, detector, *, sender, phone, counters)` + `build_scheduler(job, *, hour, minute, tz)` (AsyncIOScheduler, cron, `max_instances=1`, `coalesce=True`). | Create (Task 4) |
| `src/invoicer/app.py` | `create_app(*, settings)` fabryka: graf durable (Claude/Fakturownia/Twilio z env) + registry; montuje `/whatsapp/inbound` (reuzycie `create_inbound_app`), `/health`, `/status`; lifespan startuje scheduler. Eksponuje `app` dla uvicorn. | Create (Task 5) |
| `Dockerfile` | `python:3.12-slim` + uv; instal z `uv.lock`; ENV `PYTHONPATH=/app/src` `PORT=8080`; CMD `uvicorn invoicer.app:app --host 0.0.0.0 --port 8080`. | Create (Task 6) |
| `.dockerignore` | `.git`, `.venv`, `__pycache__`, `.pytest_cache`, `.ruff_cache`, `tests/`, `docs/`, `scripts/`, `*.sqlite`, `ledger.jsonl`, `.env*`. | Create (Task 6) |
| `fly.toml` | app, region `waw`, 1 maszyna **always-on** (`auto_stop_machines=false`, `min_machines_running=1`), `[mounts] /data`, `[http_service] internal_port=8080`, healthcheck `GET /health`. | Create (Task 7) |
| `README.md` | + sekcja `## Deploy (Fly.io)`: `fly launch`/`volumes create`/`secrets set`/`deploy`, wstawienie URL webhooka w Twilio, jednorazowy `GMAIL_TOKEN_B64`. | Modify (Task 8) |
| `pyproject.toml` | + `apscheduler>=3.10`. | Modify (Task 0) |

**Plan komendy uruchamiac z `/Users/mski/Developer/Invoicer`.** `pytest` ma `pythonpath=["src"]`.

---

### Task 0: Branch baseline + zaleznosc APScheduler

**Files:**
- Modify: `pyproject.toml`

- [ ] **Step 1: Branch + baseline**

```bash
git checkout feat/deploy-observability
git status --short                # spodziewane: ?? scripts/, tests/live/fixtures/, /data,/ledger.jsonl gdyby istnialy
uv run ruff check .               # All checks passed!
uv run pytest -q | tail -2        # 205 passed, 7 skipped (lub wiecej po hotfix)
```

Jezeli sa nieoczekiwane modyfikacje, zatrzymaj sie i zglos status.

- [ ] **Step 2: Dodaj `apscheduler` do `pyproject.toml`**

Znajdz blok `dependencies = [...]` (linie 6–20 w pyproject.toml) i dodaj jeden nowy wpis (zachowaj porzadek alfabetyczny — wstaw przed `fastapi`):

```toml
    "apscheduler>=3.10",
    "fastapi>=0.138.0",
```

- [ ] **Step 3: Synchronizacja zaleznosci**

```bash
uv sync
```
Expected: zainstalowane `apscheduler` (i `tzlocal` jako transitive). `uv.lock` zaktualizowany.

- [ ] **Step 4: Sanity-check import**

```bash
uv run python -c "from apscheduler.schedulers.asyncio import AsyncIOScheduler; print('ok')"
```
Expected: `ok`.

- [ ] **Step 5: Commit**

```bash
git add pyproject.toml uv.lock
git commit -m "deps(runtime): add apscheduler for in-process daily intake"
```

---

### Task 1: `PendingApprovals.count_pending()` (do /status)

**Files:**
- Modify: `src/invoicer/approvals.py`
- Test: `tests/unit/test_pending_approvals.py`

- [ ] **Step 1: Failing test**

Dopisz na koncu `tests/unit/test_pending_approvals.py`:

```python
def test_count_pending_counts_only_pending_status(tmp_path):
    reg = PendingApprovals(str(tmp_path / "p.sqlite"))
    reg.add("t1", "whatsapp:+48111")
    reg.add("t2", "whatsapp:+48111")
    reg.add("t3", "whatsapp:+48222")
    assert reg.count_pending() == 3
    assert reg.count_pending(phone="whatsapp:+48111") == 2
    reg.resolve_oldest("whatsapp:+48111")  # -> "t1" resolved
    assert reg.count_pending() == 2
    assert reg.count_pending(phone="whatsapp:+48111") == 1
```

Sprawdz, ze `from invoicer.approvals import PendingApprovals` juz jest u gory pliku — jesli nie, dopisz.

- [ ] **Step 2: Verify fail**

```bash
uv run pytest tests/unit/test_pending_approvals.py::test_count_pending_counts_only_pending_status -v
```
Expected: FAIL — `AttributeError: 'PendingApprovals' object has no attribute 'count_pending'`.

- [ ] **Step 3: Add method to `PendingApprovals`**

W `src/invoicer/approvals.py` po `resolve_oldest` (na koncu klasy) dodaj:

```python
    def count_pending(self, *, phone: str | None = None) -> int:
        """Liczba wpisow ze statusem 'pending' (opcjonalnie filtrowana po numerze)."""
        if phone is None:
            row = self._conn.execute(
                "SELECT COUNT(*) FROM pending_approvals WHERE status = 'pending'"
            ).fetchone()
        else:
            row = self._conn.execute(
                "SELECT COUNT(*) FROM pending_approvals "
                "WHERE status = 'pending' AND sender_phone = ?",
                (phone,),
            ).fetchone()
        return int(row[0]) if row else 0
```

- [ ] **Step 4: Verify pass**

```bash
uv run pytest tests/unit/test_pending_approvals.py -v
```
Expected: wszystkie testy zielone (istniejace + nowy).

- [ ] **Step 5: Commit**

```bash
git add src/invoicer/approvals.py tests/unit/test_pending_approvals.py
git commit -m "feat(approvals): count_pending() for /status"
```

---

### Task 1B: `TwilioWhatsAppChannel.notify` (przygotowanie pod alerty Planu 2)

**Files:**
- Modify: `src/invoicer/adapters/twilio_whatsapp.py`
- Test: `tests/unit/test_twilio_whatsapp.py` (utworz, jezeli nie istnieje; sprawdz pierwszym krokiem)

Nowa metoda do wysylki dowolnej wiadomosci WhatsApp (alert/notyfikacja). Plan 2 ja wykorzysta w `send_failure_alert`. Tu tylko czysta metoda + test wiringu (URL/auth/From/To/Body).

- [ ] **Step 1: Sprawdz, czy test file istnieje**

```bash
ls tests/unit/test_twilio_whatsapp.py 2>/dev/null && echo "istnieje" || echo "do utworzenia"
```

- [ ] **Step 2: Failing test**

Jezeli pliku nie ma, utworz `tests/unit/test_twilio_whatsapp.py`:

```python
from invoicer.adapters.twilio_whatsapp import TwilioError, TwilioWhatsAppChannel


class _FakeResponse:
    def __init__(self, status_code: int, text: str = "") -> None:
        self.status_code = status_code
        self.text = text


class _FakeHttp:
    def __init__(self, response: _FakeResponse) -> None:
        self._response = response
        self.calls: list[tuple] = []

    def post(self, url, *, data, auth):
        self.calls.append((url, data, auth))
        return self._response


def _channel(http: _FakeHttp) -> TwilioWhatsAppChannel:
    return TwilioWhatsAppChannel(
        http,
        account_sid="ACx",
        auth_token="tok",
        from_whatsapp="whatsapp:+1415",
        to_whatsapp="whatsapp:+48999",
    )


def test_notify_posts_message_to_twilio():
    http = _FakeHttp(_FakeResponse(201))
    _channel(http).notify("⚠️ Faktura FV/1: ekstrakcja padla")
    url, data, auth = http.calls[0]
    assert url == "https://api.twilio.com/2010-04-01/Accounts/ACx/Messages.json"
    assert data == {
        "From": "whatsapp:+1415",
        "To": "whatsapp:+48999",
        "Body": "⚠️ Faktura FV/1: ekstrakcja padla",
    }
    assert auth == ("ACx", "tok")


def test_notify_raises_on_non_2xx_with_redacted_body():
    http = _FakeHttp(_FakeResponse(401, text='{"error":"Bad sid AC1234567890"}'))
    try:
        _channel(http).notify("hello")
    except TwilioError as exc:
        msg = str(exc)
        assert "401" in msg
        # sekret nie powinien wyciekac w pelnej formie do wiadomosci wyjatku
        assert "AC1234567890" not in msg
        return
    raise AssertionError("oczekiwano TwilioError")
```

Jezeli plik **istnieje** — dopisz powyzsze dwie funkcje testowe na jego koncu (z importami `TwilioError, TwilioWhatsAppChannel`, jezeli brakuje).

- [ ] **Step 3: Verify fail**

```bash
uv run pytest tests/unit/test_twilio_whatsapp.py -v
```
Expected: FAIL — `AttributeError: 'TwilioWhatsAppChannel' object has no attribute 'notify'`.

- [ ] **Step 4: Implement `notify` (wydobycie wspolnej logiki POST)**

W `src/invoicer/adapters/twilio_whatsapp.py` po `request_approval` dodaj:

```python
    def notify(self, text: str) -> None:
        """Wysyla dowolna wiadomosc WhatsApp (alert/notyfikacja) do skonfigurowanego approvera."""
        url = f"https://api.twilio.com/2010-04-01/Accounts/{self._sid}/Messages.json"
        data = {"From": self._from, "To": self._to, "Body": text}
        resp = self._client.post(url, data=data, auth=(self._sid, self._token))
        if not 200 <= resp.status_code < 300:
            snippet = redact_pii(str(resp.text))[:500]
            raise TwilioError(f"Twilio POST {url} -> {resp.status_code}: {snippet}")
```

(Jezeli implementer chce DRY — moze wyciagnac wspolny POST do `_post(body)` i reuzyc w `request_approval` + `notify`. Opcjonalne; nie jest wymagane do zaliczenia testu.)

`redact_pii` jest juz zaimportowany w pliku (uzywa go `request_approval`). Komunikat o `AC1234567890` w tescie jest formatowo jak NIP/numer telefonu — `redact_pii` to nie redaguje 1:1, ale fragment jest obciety do 500 znakow i nie zawiera tokenu auth. Asercja sprawdza, ze SID **z body odpowiedzi** nie wycieka w `str(exc)`; ponizej **wzmocnienie**: dodaj `if self._sid in snippet: snippet = "[REDACTED]"` przed sformatowaniem wyjatku.

Pelna wersja (wzmocnienie redakcji o SID):

```python
    def notify(self, text: str) -> None:
        """Wysyla dowolna wiadomosc WhatsApp (alert/notyfikacja) do skonfigurowanego approvera."""
        url = f"https://api.twilio.com/2010-04-01/Accounts/{self._sid}/Messages.json"
        data = {"From": self._from, "To": self._to, "Body": text}
        resp = self._client.post(url, data=data, auth=(self._sid, self._token))
        if not 200 <= resp.status_code < 300:
            snippet = redact_pii(str(resp.text))[:500]
            if self._sid in snippet:
                snippet = snippet.replace(self._sid, "[REDACTED_SID]")
            raise TwilioError(f"Twilio POST -> {resp.status_code}: {snippet}")
```

> Uwaga: dla spojnosci z istniejacym `request_approval`, ktora **takze** ma to ryzyko, mozesz analogicznie wzmocnic redakcje w `request_approval` (opcjonalne — wykracza poza Plan, zostawione decyzji implementera/reviewera).

- [ ] **Step 5: Verify pass**

```bash
uv run pytest tests/unit/test_twilio_whatsapp.py -v
```
Expected: PASS (wszystkie testy).

- [ ] **Step 6: Commit**

```bash
git add src/invoicer/adapters/twilio_whatsapp.py tests/unit/test_twilio_whatsapp.py
git commit -m "feat(twilio): notify(text) — generic WhatsApp send for Plan 2 alerts"
```

---

### Task 2: `bootstrap_gmail_token` (headless OAuth gotcha)

**Files:**
- Create: `src/invoicer/bootstrap.py`
- Test: `tests/unit/test_bootstrap.py`

- [ ] **Step 1: Failing test**

Utworz `tests/unit/test_bootstrap.py`:

```python
import base64

import pytest

from invoicer.bootstrap import bootstrap_gmail_token


def test_decodes_base64_env_to_destination(tmp_path, monkeypatch):
    token = b'{"refresh_token":"FAKE","token_uri":"https://oauth"}'
    monkeypatch.setenv("GMAIL_TOKEN_B64", base64.b64encode(token).decode("ascii"))
    dest = tmp_path / "token.json"
    bootstrap_gmail_token("GMAIL_TOKEN_B64", dest)
    assert dest.read_bytes() == token


def test_does_not_overwrite_existing_file(tmp_path, monkeypatch):
    dest = tmp_path / "token.json"
    dest.write_bytes(b"already-there")
    monkeypatch.setenv("GMAIL_TOKEN_B64", base64.b64encode(b"NEW").decode("ascii"))
    bootstrap_gmail_token("GMAIL_TOKEN_B64", dest)
    assert dest.read_bytes() == b"already-there"


def test_noop_when_env_absent(tmp_path, monkeypatch):
    monkeypatch.delenv("GMAIL_TOKEN_B64", raising=False)
    dest = tmp_path / "token.json"
    bootstrap_gmail_token("GMAIL_TOKEN_B64", dest)
    assert not dest.exists()


def test_raises_on_invalid_base64(tmp_path, monkeypatch):
    monkeypatch.setenv("GMAIL_TOKEN_B64", "@@@nie-base64@@@")
    dest = tmp_path / "token.json"
    with pytest.raises(ValueError, match="GMAIL_TOKEN_B64"):
        bootstrap_gmail_token("GMAIL_TOKEN_B64", dest)
```

- [ ] **Step 2: Verify fail**

```bash
uv run pytest tests/unit/test_bootstrap.py -v
```
Expected: FAIL — `ModuleNotFoundError: No module named 'invoicer.bootstrap'`.

- [ ] **Step 3: Implement**

Utworz `src/invoicer/bootstrap.py`:

```python
from __future__ import annotations

import base64
import binascii
import os
from pathlib import Path


def bootstrap_gmail_token(env_name: str, dest: Path) -> None:
    """Dekoduje base64 token Gmaila z env do pliku (idempotent; brak env -> no-op).

    Headless OAuth gotcha: token.json (z refresh-tokenem) generujemy lokalnie,
    a w kontenerze wstrzykujemy jako sekret GMAIL_TOKEN_B64; tu odtwarzamy plik.
    Nie nadpisuje istniejacego pliku — chroni przed regresja po restarcie.
    """
    payload = os.environ.get(env_name)
    if not payload:
        return
    if dest.exists():
        return
    try:
        raw = base64.b64decode(payload, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ValueError(f"{env_name}: niepoprawny base64") from exc
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(raw)
```

- [ ] **Step 4: Verify pass**

```bash
uv run pytest tests/unit/test_bootstrap.py -v
```
Expected: 4 passed.

- [ ] **Step 5: Commit**

```bash
git add src/invoicer/bootstrap.py tests/unit/test_bootstrap.py
git commit -m "feat(bootstrap): decode GMAIL_TOKEN_B64 -> token file (headless OAuth)"
```

---

### Task 3: `pipeline_status` (do /status)

**Files:**
- Create: `src/invoicer/observability_status.py`
- Test: `tests/unit/test_observability_status.py`

- [ ] **Step 1: Failing test**

Utworz `tests/unit/test_observability_status.py`:

```python
from dataclasses import dataclass

import pytest

from invoicer.observability import LlmCall, LlmMetrics
from invoicer.observability_status import PipelineCounters, pipeline_status


@dataclass
class _FakeRegistry:
    pending: int

    def count_pending(self, *, phone: str | None = None) -> int:
        return self.pending


def test_pipeline_status_combines_llm_totals_counters_and_pending():
    metrics = LlmMetrics()
    metrics.record(LlmCall("claude-sonnet-4-6", 100, 20, 0.0006, 500))
    counters = PipelineCounters(processed=3, failed=1)
    registry = _FakeRegistry(pending=2)

    st = pipeline_status(metrics, counters, registry, phone="whatsapp:+48111")

    assert st["llm"]["n_calls"] == 1
    assert st["llm"]["input_tokens"] == 100
    assert st["pipeline"]["processed"] == 3
    assert st["pipeline"]["failed"] == 1
    assert st["pipeline"]["pending"] == 2


def test_pipeline_status_phone_filter_passed_to_registry():
    captured = {}

    class _Reg:
        def count_pending(self, *, phone=None):
            captured["phone"] = phone
            return 0

    pipeline_status(LlmMetrics(), PipelineCounters(), _Reg(), phone="whatsapp:+48111")
    assert captured["phone"] == "whatsapp:+48111"


def test_counters_default_zero():
    c = PipelineCounters()
    assert c.processed == 0 and c.failed == 0
    c.incr_processed()
    c.incr_failed()
    c.incr_failed()
    assert c.processed == 1 and c.failed == 2
```

- [ ] **Step 2: Verify fail**

```bash
uv run pytest tests/unit/test_observability_status.py -v
```
Expected: FAIL — `ModuleNotFoundError: No module named 'invoicer.observability_status'`.

- [ ] **Step 3: Implement**

Utworz `src/invoicer/observability_status.py`:

```python
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from invoicer.observability import LlmMetrics


class _Registry(Protocol):
    def count_pending(self, *, phone: str | None = None) -> int: ...


@dataclass
class PipelineCounters:
    """In-memory liczniki pipeline'u (reset przy restarcie — biezacy podglad)."""

    processed: int = 0
    failed: int = 0

    def incr_processed(self) -> None:
        self.processed += 1

    def incr_failed(self) -> None:
        self.failed += 1


def pipeline_status(
    metrics: LlmMetrics,
    counters: PipelineCounters,
    registry: _Registry,
    *,
    phone: str | None = None,
) -> dict:
    """Agreguje stan dla GET /status: koszt/latencja LLM + liczniki + pending."""
    return {
        "llm": metrics.totals(),
        "pipeline": {
            "processed": counters.processed,
            "failed": counters.failed,
            "pending": registry.count_pending(phone=phone),
        },
    }
```

- [ ] **Step 4: Verify pass**

```bash
uv run pytest tests/unit/test_observability_status.py -v
```
Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add src/invoicer/observability_status.py tests/unit/test_observability_status.py
git commit -m "feat(observability): pipeline_status + PipelineCounters (for /status)"
```

---

### Task 4: Scheduler — `run_daily_intake` + `build_scheduler`

**Files:**
- Create: `src/invoicer/scheduler.py`
- Test: `tests/unit/test_scheduler.py`

- [ ] **Step 1: Failing test**

Utworz `tests/unit/test_scheduler.py`:

```python
from __future__ import annotations

from datetime import datetime
from unittest.mock import MagicMock

from invoicer.adapters.stub_approval import StubApprovalChannel
from invoicer.adapters.stub_detector import StubInvoiceDetector
from invoicer.models import InvoiceDocument
from invoicer.observability_status import PipelineCounters
from invoicer.scheduler import build_scheduler, run_daily_intake


class _FakeSource:
    def __init__(self, docs):
        self._docs = docs

    def fetch(self, sender):
        assert sender == "owner@example.com"
        return list(self._docs)


def _doc(name: str) -> InvoiceDocument:
    return InvoiceDocument(
        sender="owner@example.com",
        received_at=datetime(2026, 6, 24),
        filename=name,
        content=b"%PDF-1.4",
    )


def test_run_daily_intake_requests_approval_per_detected_invoice():
    docs = [_doc("a.pdf"), _doc("b.pdf")]
    channel = StubApprovalChannel()
    registry = MagicMock()
    graph = MagicMock()
    counters = PipelineCounters()
    # symulujemy bramke: payload = niepusty dict (wartosci pochodzace ze stanu)
    payload = {
        "seller": "ACME",
        "seller_nip": "5260001246",
        "number": "FV/1",
        "total_gross": "1230.00",
        "currency": "PLN",
        "treatment": "krajowa",
    }

    def fake_request(graph_, channel_, registry_, document, *, thread_id, phone):
        channel_.request_approval(payload)
        registry_.add(thread_id, phone)
        return payload

    run_daily_intake(
        graph,
        channel,
        registry,
        _FakeSource(docs),
        StubInvoiceDetector(result=True),
        sender="owner@example.com",
        phone="whatsapp:+48111",
        counters=counters,
        request_fn=fake_request,
    )
    assert len(channel.sent) == 2
    assert counters.processed == 2
    assert counters.failed == 0
    assert registry.add.call_count == 2


def test_run_daily_intake_skips_failed_invoice_and_continues():
    docs = [_doc("a.pdf"), _doc("b.pdf"), _doc("c.pdf")]
    channel = StubApprovalChannel()
    counters = PipelineCounters()
    calls = {"n": 0}

    def request_fn(graph, channel_, registry, document, *, thread_id, phone):
        calls["n"] += 1
        if document.filename == "b.pdf":
            raise RuntimeError("ekstrakcja padla")
        channel_.request_approval({"x": document.filename})
        return {"x": document.filename}

    run_daily_intake(
        MagicMock(),
        channel,
        MagicMock(),
        _FakeSource(docs),
        StubInvoiceDetector(result=True),
        sender="owner@example.com",
        phone="whatsapp:+48111",
        counters=counters,
        request_fn=request_fn,
    )
    assert calls["n"] == 3
    assert counters.processed == 2
    assert counters.failed == 1
    assert [m["x"] for m in channel.sent] == ["a.pdf", "c.pdf"]


def test_build_scheduler_adds_cron_job():
    sched = build_scheduler(lambda: None, hour=8, minute=0, tz="Europe/Warsaw")
    jobs = sched.get_jobs()
    assert len(jobs) == 1
    trigger = jobs[0].trigger
    assert getattr(trigger, "fields", None) is not None  # CronTrigger
    field_names = [f.name for f in trigger.fields]
    assert "hour" in field_names and "minute" in field_names
    assert str(trigger.timezone) == "Europe/Warsaw"
```

- [ ] **Step 2: Verify fail**

```bash
uv run pytest tests/unit/test_scheduler.py -v
```
Expected: FAIL — `ModuleNotFoundError: No module named 'invoicer.scheduler'`.

- [ ] **Step 3: Implement**

Utworz `src/invoicer/scheduler.py`:

```python
from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from invoicer.observability_status import PipelineCounters
from invoicer.ports import EmailSource, InvoiceDetector
from invoicer.runner import fetch_invoice_documents, request_invoice_approval

_logger = logging.getLogger("invoicer.scheduler")


def run_daily_intake(
    graph: Any,
    channel: Any,
    registry: Any,
    source: EmailSource,
    detector: InvoiceDetector,
    *,
    sender: str,
    phone: str,
    counters: PipelineCounters,
    request_fn: Callable[..., dict | None] = request_invoice_approval,
) -> None:
    """Codzienny zaciag: Gmail -> detekcja -> per-faktura request akceptacji.

    Per-faktura try/except: jedna zla faktura nie blokuje pozostalych.
    `request_fn` wstrzykiwany (testy/CI: stub). thread_id generowany lokalnie.
    """
    import uuid

    docs = fetch_invoice_documents(source, detector, sender)
    _logger.info("intake start: %d faktur do przetworzenia", len(docs))
    for doc in docs:
        thread_id = f"intake-{uuid.uuid4()}"
        try:
            request_fn(graph, channel, registry, doc, thread_id=thread_id, phone=phone)
            counters.incr_processed()
        except Exception:
            counters.incr_failed()
            # nie podnosimy — kolejna faktura ma sie przetworzyc; szczegoly w Sentry/log (Plan 2)
            _logger.exception("intake: faktura %s nie przeszla", doc.filename)
    _logger.info(
        "intake done: processed=%d failed=%d", counters.processed, counters.failed
    )


def build_scheduler(
    job: Callable[[], None], *, hour: int, minute: int, tz: str
) -> AsyncIOScheduler:
    """Buduje AsyncIOScheduler z jednym cron-jobem; coalesce + max_instances=1."""
    sched = AsyncIOScheduler(timezone=tz)
    sched.add_job(
        job,
        CronTrigger(hour=hour, minute=minute, timezone=tz),
        id="daily-intake",
        max_instances=1,
        coalesce=True,
        misfire_grace_time=3600,
    )
    return sched
```

- [ ] **Step 4: Verify pass**

```bash
uv run pytest tests/unit/test_scheduler.py -v
```
Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add src/invoicer/scheduler.py tests/unit/test_scheduler.py
git commit -m "feat(scheduler): run_daily_intake + build_scheduler (APScheduler cron)"
```

---

### Task 5: `app.py` — fabryka FastAPI (webhook + /health + /status + lifespan)

**Files:**
- Create: `src/invoicer/app.py`
- Test: `tests/unit/test_app.py`

- [ ] **Step 1: Failing test**

Utworz `tests/unit/test_app.py`:

```python
from __future__ import annotations

from fastapi.testclient import TestClient

from invoicer.app import AppSettings, create_app


def _settings(tmp_path) -> AppSettings:
    return AppSettings(
        approver_phone="whatsapp:+48111",
        gmail_sender="owner@example.com",
        intake_hour=8,
        intake_minute=0,
        intake_tz="Europe/Warsaw",
        data_dir=tmp_path,
        # tryb testowy: bez realnych adapterow / scheduler nie startuje
        test_mode=True,
    )


def test_health_returns_200(tmp_path):
    app = create_app(settings=_settings(tmp_path))
    with TestClient(app) as client:
        r = client.get("/health")
        assert r.status_code == 200
        assert r.json()["status"] == "ok"


def test_status_returns_llm_and_pipeline(tmp_path):
    app = create_app(settings=_settings(tmp_path))
    with TestClient(app) as client:
        r = client.get("/status")
        assert r.status_code == 200
        body = r.json()
        assert "llm" in body and "pipeline" in body
        assert body["pipeline"]["pending"] == 0
        assert body["pipeline"]["processed"] == 0
        assert body["pipeline"]["failed"] == 0


def test_inbound_returns_no_pending_for_unknown_phone(tmp_path):
    app = create_app(settings=_settings(tmp_path))
    with TestClient(app) as client:
        r = client.post(
            "/whatsapp/inbound",
            data={"From": "whatsapp:+48999", "Body": "TAK"},
        )
        assert r.status_code == 200
        assert r.json() == {"status": "no_pending"}


def test_inbound_ignored_for_unknown_body(tmp_path):
    app = create_app(settings=_settings(tmp_path))
    with TestClient(app) as client:
        r = client.post(
            "/whatsapp/inbound",
            data={"From": "whatsapp:+48111", "Body": "?"},
        )
        assert r.json() == {"status": "ignored"}
```

- [ ] **Step 2: Verify fail**

```bash
uv run pytest tests/unit/test_app.py -v
```
Expected: FAIL — `ModuleNotFoundError: No module named 'invoicer.app'`.

- [ ] **Step 3: Implement fabryka**

Utworz `src/invoicer/app.py`:

```python
from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path

from fastapi import FastAPI

from invoicer.adapters.mock_subiekt import MockSubiektSink
from invoicer.adapters.stub_detector import StubInvoiceDetector
from invoicer.adapters.stub_extractor import StubExtractor
from invoicer.adapters.stub_reasoner import IdentityReasoner
from invoicer.approvals import PendingApprovals
from invoicer.bootstrap import bootstrap_gmail_token
from invoicer.graph.build import build_invoice_graph
from invoicer.ledger import Ledger
from invoicer.observability import LlmMetrics
from invoicer.observability_status import PipelineCounters, pipeline_status
from invoicer.runner import _demo_invoice, persistent_checkpointer
from invoicer.scheduler import build_scheduler, run_daily_intake
from invoicer.security import install_redaction
from invoicer.webhook import create_inbound_app


@dataclass
class AppSettings:
    approver_phone: str
    gmail_sender: str
    intake_hour: int = 8
    intake_minute: int = 0
    intake_tz: str = "Europe/Warsaw"
    data_dir: Path = Path("/data")
    test_mode: bool = False  # True w testach: stuby + scheduler nie startuje


def _settings_from_env() -> AppSettings:
    return AppSettings(
        approver_phone=os.environ["APPROVER_WHATSAPP_TO"],
        gmail_sender=os.environ["GMAIL_SENDER_FILTER"],
        intake_hour=int(os.getenv("INTAKE_HOUR", "8")),
        intake_minute=int(os.getenv("INTAKE_MINUTE", "0")),
        intake_tz=os.getenv("INTAKE_TZ", "Europe/Warsaw"),
        data_dir=Path(os.getenv("INVOICER_DATA_DIR", "/data")),
    )


def _build_real_graph(settings: AppSettings, checkpointer):
    """Realne adaptery: Claude + Fakturownia (lub MockSubiekt) + ledger na wolumenie."""
    from invoicer.adapters.claude_extractor import ClaudeVisionExtractor
    from invoicer.adapters.claude_reasoner import ClaudeExceptionReasoner

    if os.getenv("INVOICER_SINK", "").lower() == "fakturownia":
        from invoicer.adapters.fakturownia import build_fakturownia_sink

        sink = build_fakturownia_sink()
    else:
        sink = MockSubiektSink()
    return build_invoice_graph(
        extractor=ClaudeVisionExtractor(),
        reasoner=ClaudeExceptionReasoner(),
        ledger=Ledger(settings.data_dir / "ledger.jsonl"),
        sink=sink,
        checkpointer=checkpointer,
    )


def _build_test_graph(settings: AppSettings, checkpointer):
    return build_invoice_graph(
        extractor=StubExtractor(_demo_invoice()),
        reasoner=IdentityReasoner(),
        ledger=Ledger(settings.data_dir / "ledger.jsonl"),
        sink=MockSubiektSink(),
        checkpointer=checkpointer,
    )


def create_app(*, settings: AppSettings | None = None) -> FastAPI:
    """Fabryka aplikacji: durable graf + registry + webhook + /health + /status + scheduler."""
    settings = settings or _settings_from_env()
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    install_redaction(logging.getLogger("invoicer"))

    if not settings.test_mode:
        bootstrap_gmail_token("GMAIL_TOKEN_B64", settings.data_dir / "token.json")

    settings.data_dir.mkdir(parents=True, exist_ok=True)
    db_path = str(settings.data_dir / "invoicer_state.sqlite")
    checkpointer = persistent_checkpointer(db_path)
    registry = PendingApprovals(db_path)
    metrics = LlmMetrics()
    counters = PipelineCounters()

    graph = (
        _build_test_graph(settings, checkpointer)
        if settings.test_mode
        else _build_real_graph(settings, checkpointer)
    )

    # webhook (reuzycie logiki Planu B) + dodatkowe endpointy
    app = create_inbound_app(graph, registry)

    @app.get("/health")
    def health() -> dict:
        return {"status": "ok"}

    @app.get("/status")
    def status() -> dict:
        return pipeline_status(metrics, counters, registry, phone=settings.approver_phone)

    if settings.test_mode:
        return app

    @asynccontextmanager
    async def _lifespan(_: FastAPI):
        from invoicer.adapters.claude_detector import ClaudeInvoiceDetector
        from invoicer.adapters.gmail import GmailAdapter
        from invoicer.adapters.gmail_auth import gmail_service_from_token
        from invoicer.adapters.twilio_whatsapp import build_twilio_whatsapp_channel

        channel = build_twilio_whatsapp_channel()

        def _job() -> None:
            service = gmail_service_from_token(settings.data_dir / "token.json")
            run_daily_intake(
                graph,
                channel,
                registry,
                GmailAdapter(service),
                ClaudeInvoiceDetector(),
                sender=settings.gmail_sender,
                phone=settings.approver_phone,
                counters=counters,
            )

        scheduler = build_scheduler(
            _job,
            hour=settings.intake_hour,
            minute=settings.intake_minute,
            tz=settings.intake_tz,
        )
        scheduler.start()
        try:
            yield
        finally:
            scheduler.shutdown(wait=False)

    app.router.lifespan_context = _lifespan
    return app


# Eksponowane dla uvicorn (invoicer.app:app).
# Tworzone leniwie wewnatrz, gdy uvicorn faktycznie laduje modul w kontenerze.
app: FastAPI | None = None


def _factory() -> FastAPI:  # uvicorn factory mode
    return create_app()
```

Uwaga: w testach uzywamy `create_app(settings=...)` bezposrednio. Uvicorn w kontenerze startujemy przez **factory**: `uvicorn invoicer.app:_factory --factory --host 0.0.0.0 --port 8080` (Task 6).

- [ ] **Step 4: Verify pass**

```bash
uv run pytest tests/unit/test_app.py -v
```
Expected: 4 passed.

- [ ] **Step 5: Commit**

```bash
git add src/invoicer/app.py tests/unit/test_app.py
git commit -m "feat(app): FastAPI factory — webhook + /health + /status + lifespan scheduler"
```

---

### Task 6: `Dockerfile` + `.dockerignore`

**Files:**
- Create: `Dockerfile`
- Create: `.dockerignore`

Nie testujemy budowy obrazu w CI (zbyt wolne i bez Dockera), ale weryfikujemy poprawnosc syntaktyczna i poprawne dzialanie lintera.

- [ ] **Step 1: Utworz `Dockerfile`**

Plik `Dockerfile` w korzeniu repo:

```dockerfile
# syntax=docker/dockerfile:1.7
FROM python:3.12-slim AS base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    UV_LINK_MODE=copy \
    PYTHONPATH=/app/src \
    PORT=8080

# uv (pinned via setup-uv image stage)
COPY --from=ghcr.io/astral-sh/uv:0.5.11 /uv /usr/local/bin/uv

WORKDIR /app

# 1) Dependency layer (cache-friendly)
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

# 2) App source
COPY src ./src

# 3) Runtime
EXPOSE 8080
CMD ["uv", "run", "uvicorn", "invoicer.app:_factory", "--factory", "--host", "0.0.0.0", "--port", "8080"]
```

- [ ] **Step 2: Utworz `.dockerignore`**

Plik `.dockerignore`:

```
.git
.gitignore
.venv
__pycache__
*.pyc
.pytest_cache
.ruff_cache
tests
docs
scripts
streamlit_session
*.sqlite
ledger.jsonl
.env*
token.json
client_secret*.json
```

- [ ] **Step 3: Weryfikacja Dockerfile (syntaktyczna + sanity)**

```bash
test -f Dockerfile && test -f .dockerignore && echo "pliki istnieja"
grep -q "uvicorn invoicer.app:_factory" Dockerfile && echo "factory ok"
grep -q "uv sync --frozen --no-dev" Dockerfile && echo "deps ok"
```
Expected: trzy "ok"/"istnieja".

- [ ] **Step 4: Commit**

```bash
git add Dockerfile .dockerignore
git commit -m "build(docker): slim python3.12 image with uv + uvicorn factory mode"
```

---

### Task 7: `fly.toml` (Fly.io: always-on + wolumen + healthcheck)

**Files:**
- Create: `fly.toml`

- [ ] **Step 1: Utworz `fly.toml`**

Plik `fly.toml` w korzeniu repo. **Nazwa apki:** `invoicer-app` (jezeli zajęta — uzytkownik zmieni przy `fly launch`).

```toml
# Fly.io config: jeden zawsze-zywy kontener, wolumen /data, healthcheck /health.
app = "invoicer-app"
primary_region = "waw"

[build]

[env]
  INVOICER_DATA_DIR = "/data"
  PORT = "8080"
  INTAKE_HOUR = "8"
  INTAKE_MINUTE = "0"
  INTAKE_TZ = "Europe/Warsaw"

[[mounts]]
  source = "invoicer_data"
  destination = "/data"

[http_service]
  internal_port = 8080
  force_https = true
  auto_stop_machines = false
  auto_start_machines = true
  min_machines_running = 1
  processes = ["app"]

  [[http_service.checks]]
    grace_period = "10s"
    interval = "30s"
    method = "GET"
    timeout = "5s"
    path = "/health"

[[vm]]
  cpu_kind = "shared"
  cpus = 1
  memory_mb = 512
```

- [ ] **Step 2: Weryfikacja**

```bash
test -f fly.toml && echo "fly.toml istnieje"
grep -q "auto_stop_machines = false" fly.toml && echo "always-on ok"
grep -q "min_machines_running = 1" fly.toml && echo "min=1 ok"
grep -q 'destination = "/data"' fly.toml && echo "wolumen /data ok"
grep -q 'path = "/health"' fly.toml && echo "healthcheck /health ok"
```
Expected: piec "ok"/"istnieje".

- [ ] **Step 3: Commit**

```bash
git add fly.toml
git commit -m "deploy(fly): always-on app + /data volume + /health checks (waw)"
```

---

### Task 8: README — sekcja Deploy

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Znajdz miejsce na sekcje**

```bash
grep -n "^## " README.md | head
```

Wstawiamy nowa sekcje **na koncu** README (lub przed pierwsza sekcja, ktora dotyczy uruchamiania lokalnego — zostaw to do oceny implementera; nizej dostarczamy gotowa tresc).

- [ ] **Step 2: Dopisz sekcje Deploy (jednorazowo, do konca pliku)**

Dopisz na koncu `README.md`:

```markdown
## Deploy (Fly.io)

Agent dziala 24/7 jako jeden zawsze-zywy serwis: realny webhook `POST /whatsapp/inbound`
+ in-process scheduler codziennego zaciagu (08:00 Europe/Warsaw) + trwala SQLite na wolumenie.

### 1. Jednorazowy setup

```bash
# 1) Konto Fly + CLI
brew install flyctl
fly auth login

# 2) Utworz aplikacje z istniejacego fly.toml (NIE generuj nowego)
fly launch --copy-config --no-deploy

# 3) Wolumen na /data (region zgodny z fly.toml, np. waw)
fly volumes create invoicer_data --region waw --size 1

# 4) Sekrety (wszystkie za jednym razem)
fly secrets set \
  ANTHROPIC_API_KEY="..." \
  TWILIO_ACCOUNT_SID="AC..." \
  TWILIO_AUTH_TOKEN="..." \
  TWILIO_WHATSAPP_FROM="whatsapp:+14155238886" \
  APPROVER_WHATSAPP_TO="whatsapp:+48..." \
  FAKTUROWNIA_API_TOKEN="..." \
  FAKTUROWNIA_DOMAIN="mstudniarski" \
  GMAIL_SENDER_FILTER="m.studniarski@gmail.com" \
  INVOICER_SINK="fakturownia"

# 5) Gmail token (headless): zakoduj lokalny token.json -> sekret
fly secrets set GMAIL_TOKEN_B64="$(base64 -i token.json)"
```

### 2. Deploy

```bash
fly deploy
fly logs       # logi na zywo
curl -s https://<twoj-app>.fly.dev/health
curl -s https://<twoj-app>.fly.dev/status | jq
```

### 3. Webhook WhatsApp w Twilio

Twilio Console -> Messaging -> Sandbox -> "When a message comes in":

```
https://<twoj-app>.fly.dev/whatsapp/inbound   (POST)
```

### 4. Aktualizacje

`fly deploy` po kazdej zmianie w `main`. Maszyna restartuje (rolling); stan na `/data` przezywa.
Auto-deploy z CI/CD = osobny plan (Plan 2).

### Rotacja tokenu Gmail

Refresh-token jest dlugozyjacy. Jezeli wygasnie:
1. Uruchom lokalnie `authorize_gmail(...)` -> nowy `token.json`.
2. `fly secrets set GMAIL_TOKEN_B64="$(base64 -i token.json)"`.
3. `fly deploy` (restart maszyny ladujacy nowy token).
```

- [ ] **Step 3: Weryfikacja**

```bash
grep -q "Deploy (Fly.io)" README.md && echo "ok"
grep -q "GMAIL_TOKEN_B64" README.md && echo "ok"
grep -q "/whatsapp/inbound" README.md && echo "ok"
```
Expected: trzy "ok".

- [ ] **Step 4: Commit**

```bash
git add README.md
git commit -m "docs(deploy): README — Fly.io setup, secrets, GMAIL_TOKEN_B64, Twilio webhook"
```

---

### Task 9: Lint + pelny suite

**Files:** brak nowych — pelna weryfikacja.

- [ ] **Step 1: Ruff lint**

```bash
uv run ruff check .
```
Expected: `All checks passed!`

- [ ] **Step 2: Ruff format check**

```bash
uv run ruff format --check .
```
Expected: brak plikow do przeformatowania. Gdy chce zmiany: `uv run ruff format .`, potem powtorz krok 1.

- [ ] **Step 3: Full test suite**

```bash
uv run pytest -q
```
Expected: wszystkie zielone, **baseline + ~17 nowych testow** (Task 1 +1, Task 2 +4, Task 3 +3, Task 4 +3, Task 5 +4, fly/dockerfile bez testow). Zero failed.

- [ ] **Step 4: Commit (jezeli format cos zmienil; inaczej pomin)**

```bash
git status --short    # pusty -> pomin
git add -A
git commit -m "chore(runtime): ruff format"
```

---

### Task 10: Manualny smoke deploy (live, udokumentowany — nie commit)

> **Uwaga:** ten krok wymaga konta Fly i Twoich sekretow. Wykonujesz go raz, recznie, po ukonczeniu Tasks 0–9 i finalowym review opus + merge `--no-ff` do `main`. NIE wpisuj wynikow do gita.

- [ ] **Krok 1:** `fly launch --copy-config --no-deploy` (jezeli pierwszy raz).
- [ ] **Krok 2:** `fly volumes create invoicer_data --region waw --size 1`.
- [ ] **Krok 3:** `fly secrets set ... GMAIL_TOKEN_B64="$(base64 -i token.json)"` (wszystkie sekrety z README).
- [ ] **Krok 4:** `fly deploy`.
- [ ] **Krok 5:** `curl -s https://<app>.fly.dev/health` -> `{"status":"ok"}`.
- [ ] **Krok 6:** `curl -s https://<app>.fly.dev/status | jq` -> klucze `llm` i `pipeline`.
- [ ] **Krok 7:** Wklej URL webhooka do Twilio Sandbox.
- [ ] **Krok 8:** `fly logs` -> obserwuj kolejny zaciag o 08:00 Europe/Warsaw (lub uruchom recznie skryptem `whatsapp_approval.py` jak dotad — Plan 2 doda alerty/Sentry).

---

## Po wykonaniu planu

Finalowy review (opus) calego brancha `feat/deploy-observability` (Plan 1), nastepnie `git checkout main && git merge --no-ff feat/deploy-observability` (zgodnie z przyjetym przeplywem per-feature).

Plan 2 (Sentry + alerty + CI/CD auto-deploy) bedzie pisany **po** merge Planu 1, na nowej galezi `feat/deploy-observability-2` z `main`.
