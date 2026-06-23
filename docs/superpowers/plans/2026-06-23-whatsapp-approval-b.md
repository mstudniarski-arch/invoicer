# Invoicer — Plan B: WhatsApp approval — przychodzące (webhook + resume) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Domknąć dwukierunkowy approve: odpowiedź „TAK/NIE" z WhatsApp (webhook Twilio) wznawia zapauzowany graf i księguje — przez rejestr `PendingApprovals` (numer→`thread_id`) i orkiestrację.

**Architecture:** Trwały rejestr `PendingApprovals` (SQLite, FIFO via rowid) mapuje numer telefonu na `thread_id`. Webhook `FastAPI` (`POST /whatsapp/inbound`) parsuje treść odpowiedzi (`parse_decision`), bierze najstarszy pending dla numeru i wywołuje `resume_document`. Orkiestrator `request_invoice_approval` spina: `start_document` (durable graf z Planu A) → `registry.add` → `channel.request_approval`. To Plan B z 2 (Plan A = wychodzące + durable checkpointer — scalony).

**Tech Stack:** Python 3.12, uv, `fastapi`/`python-multipart`/`uvicorn`, `sqlite3` (stdlib), LangGraph, pytest (`fastapi.testclient.TestClient`), ruff.

**Spec:** `docs/superpowers/specs/2026-06-23-whatsapp-approval-design.md` (§3.4–3.6).

**Stan wyjściowy:** `main` po Planie A. Dostępne: `persistent_checkpointer(db_path) -> SqliteSaver`, `start_document`/`resume_document` (w `runner.py`); port `ApprovalChannel` + `StubApprovalChannel` + `TwilioWhatsAppChannel`; payload `human_review` ma `seller_nip`. `resume_document(graph, *, thread_id, decision)`. **Gałąź `feat/whatsapp-approval-b` utworzona.** `fastapi`/`python-multipart`/`uvicorn` już dodane (working tree). **Zweryfikowane empirycznie:** `PendingApprovals` FIFO przez `rowid`; webhook `Form(...)` + `TestClient` (POST `From`/`Body`) działają. Baseline: 176 passed, 6 skipped, ruff czysty. `[tool.uv] package=false` (`pythonpath=src`).

---

## File Structure

| Plik | Odpowiedzialność |
|------|------------------|
| `pyproject.toml`/`uv.lock` (MOD) | + `fastapi`, `python-multipart`, `uvicorn`. |
| `src/invoicer/approvals.py` (NEW) | `PendingApprovals` (rejestr numer→thread_id, SQLite, FIFO). |
| `src/invoicer/webhook.py` (NEW) | `parse_decision`, `create_inbound_app` (FastAPI `/whatsapp/inbound`). |
| `src/invoicer/runner.py` (MOD) | `request_invoice_approval` (orkiestracja). |
| `tests/unit/test_pending_approvals.py` (NEW) | rejestr FIFO/izolacja numerów. |
| `tests/unit/test_webhook.py` (NEW) | parse_decision + webhook (TestClient, fake registry/resume). |
| `tests/unit/test_runner.py` (MOD) | `request_invoice_approval`. |

---

## Task 0: Zależności + baseline

- [ ] **Step 1** — Gałąź `feat/whatsapp-approval-b` już utworzona. Potwierdź: `cd /Users/mski/Developer/Invoicer && git branch --show-current`.
- [ ] **Step 2: Baseline** — `uv run pytest -q` → `176 passed, 6 skipped`. `uv run ruff check .` → clean.
- [ ] **Step 3: Zależności** — `uv add fastapi python-multipart uvicorn` (idempotentne — mogą już być). Sanity: `uv run python -c "import fastapi, multipart, uvicorn; print('ok')"` → `ok`.
- [ ] **Step 4: Commit**
```bash
git add pyproject.toml uv.lock
git commit -m "build: add fastapi + python-multipart + uvicorn (WhatsApp inbound webhook)"
```

---

## Task 1: `PendingApprovals` (rejestr numer→thread_id)

**Files:**
- Create: `src/invoicer/approvals.py`
- Test: `tests/unit/test_pending_approvals.py`

- [ ] **Step 1: Write failing tests** — utwórz `tests/unit/test_pending_approvals.py`:
```python
from invoicer.approvals import PendingApprovals


def _registry(tmp_path):
    return PendingApprovals(str(tmp_path / "pending.sqlite"))


def test_resolve_oldest_returns_fifo_then_none(tmp_path):
    reg = _registry(tmp_path)
    reg.add("t1", "whatsapp:+48500")
    reg.add("t2", "whatsapp:+48500")
    assert reg.resolve_oldest("whatsapp:+48500") == "t1"
    assert reg.resolve_oldest("whatsapp:+48500") == "t2"
    assert reg.resolve_oldest("whatsapp:+48500") is None


def test_resolve_oldest_unknown_phone_returns_none(tmp_path):
    assert _registry(tmp_path).resolve_oldest("whatsapp:+999") is None


def test_phones_are_isolated(tmp_path):
    reg = _registry(tmp_path)
    reg.add("a1", "whatsapp:+48500")
    reg.add("b1", "whatsapp:+48600")
    assert reg.resolve_oldest("whatsapp:+48600") == "b1"
    assert reg.resolve_oldest("whatsapp:+48500") == "a1"
```

- [ ] **Step 2: Run to verify it fails** — `uv run pytest tests/unit/test_pending_approvals.py -v` → FAIL (`ModuleNotFoundError: No module named 'invoicer.approvals'`).

- [ ] **Step 3: Implement** — utwórz `src/invoicer/approvals.py`:
```python
from __future__ import annotations

import sqlite3


class PendingApprovals:
    """Trwaly rejestr oczekujacych akceptacji: numer telefonu -> thread_id (FIFO via rowid).

    Mapuje przychodzaca odpowiedz WhatsApp (po numerze nadawcy) na thread do wznowienia.
    check_same_thread=False: webhook (inny watek/proces) korzysta z tego samego pliku.
    """

    def __init__(self, db_path: str) -> None:
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.execute(
            "CREATE TABLE IF NOT EXISTS pending_approvals ("
            "thread_id TEXT NOT NULL, sender_phone TEXT NOT NULL, "
            "status TEXT NOT NULL DEFAULT 'pending')"
        )
        self._conn.commit()

    def add(self, thread_id: str, phone: str) -> None:
        self._conn.execute(
            "INSERT INTO pending_approvals (thread_id, sender_phone, status) "
            "VALUES (?, ?, 'pending')",
            (thread_id, phone),
        )
        self._conn.commit()

    def resolve_oldest(self, phone: str) -> str | None:
        """Zwraca thread_id najstarszego PENDING dla numeru i oznacza go RESOLVED (FIFO)."""
        row = self._conn.execute(
            "SELECT rowid, thread_id FROM pending_approvals "
            "WHERE sender_phone = ? AND status = 'pending' ORDER BY rowid LIMIT 1",
            (phone,),
        ).fetchone()
        if row is None:
            return None
        rowid, thread_id = row
        self._conn.execute(
            "UPDATE pending_approvals SET status = 'resolved' WHERE rowid = ?", (rowid,)
        )
        self._conn.commit()
        return thread_id
```

- [ ] **Step 4: Verify pass** — `uv run pytest tests/unit/test_pending_approvals.py -v` → PASS (3). `uv run pytest -q` → green (179 passed, 6 skipped). `uv run ruff check . && uv run ruff format --check .` → clean.

- [ ] **Step 5: Commit**
```bash
git add src/invoicer/approvals.py tests/unit/test_pending_approvals.py
git commit -m "feat: PendingApprovals registry (phone -> thread_id, SQLite FIFO)"
```

---

## Task 2: Webhook `FastAPI` (`/whatsapp/inbound`)

**Files:**
- Create: `src/invoicer/webhook.py`
- Test: `tests/unit/test_webhook.py`

- [ ] **Step 1: Write failing tests** — utwórz `tests/unit/test_webhook.py`:
```python
from fastapi.testclient import TestClient

from invoicer.webhook import create_inbound_app, parse_decision


class _FakeRegistry:
    def __init__(self, mapping):
        self._mapping = dict(mapping)
        self.resolved: list[str] = []

    def resolve_oldest(self, phone):
        self.resolved.append(phone)
        return self._mapping.get(phone)


def _client(registry, resumes):
    def _resume(graph, *, thread_id, decision):
        resumes.append((thread_id, decision))

    app = create_inbound_app(graph=object(), registry=registry, resume=_resume)
    return TestClient(app)


def test_parse_decision_variants():
    assert parse_decision("TAK") == "approve"
    assert parse_decision(" yes ") == "approve"
    assert parse_decision("1") == "approve"
    assert parse_decision("NIE") == "reject"
    assert parse_decision("2") == "reject"
    assert parse_decision("co?") is None


def test_inbound_approve_resumes_oldest_thread():
    reg = _FakeRegistry({"whatsapp:+48500": "t1"})
    resumes: list = []
    resp = _client(reg, resumes).post(
        "/whatsapp/inbound", data={"From": "whatsapp:+48500", "Body": "TAK"}
    )
    assert resp.json()["status"] == "resumed"
    assert resumes == [("t1", "approve")]


def test_inbound_reject_resumes_with_reject():
    reg = _FakeRegistry({"whatsapp:+48500": "t1"})
    resumes: list = []
    _client(reg, resumes).post("/whatsapp/inbound", data={"From": "whatsapp:+48500", "Body": "nie"})
    assert resumes == [("t1", "reject")]


def test_inbound_unrecognized_does_not_resume():
    reg = _FakeRegistry({"whatsapp:+48500": "t1"})
    resumes: list = []
    resp = _client(reg, resumes).post(
        "/whatsapp/inbound", data={"From": "whatsapp:+48500", "Body": "moze"}
    )
    assert resp.json()["status"] == "ignored"
    assert resumes == []


def test_inbound_no_pending_does_not_resume():
    reg = _FakeRegistry({})
    resumes: list = []
    resp = _client(reg, resumes).post(
        "/whatsapp/inbound", data={"From": "whatsapp:+999", "Body": "TAK"}
    )
    assert resp.json()["status"] == "no_pending"
    assert resumes == []
```

- [ ] **Step 2: Run to verify it fails** — `uv run pytest tests/unit/test_webhook.py -v` → FAIL (`ModuleNotFoundError: No module named 'invoicer.webhook'`).

- [ ] **Step 3: Implement** — utwórz `src/invoicer/webhook.py`:
```python
from __future__ import annotations

from fastapi import FastAPI, Form

from invoicer.runner import resume_document

_APPROVE = {"tak", "yes", "approve", "1", "t"}
_REJECT = {"nie", "no", "reject", "2", "n"}


def parse_decision(body: str) -> str | None:
    """Mapuje tresc odpowiedzi WhatsApp na decyzje: 'approve' / 'reject' / None (nierozpoznane)."""
    token = body.strip().lower()
    if token in _APPROVE:
        return "approve"
    if token in _REJECT:
        return "reject"
    return None


def create_inbound_app(graph, registry, *, resume=resume_document) -> FastAPI:
    """FastAPI z webhookiem Twilio (POST /whatsapp/inbound).

    Twilio wola endpoint przy odpowiedzi WhatsApp (form: From, Body). Parsuje TAK/NIE,
    bierze najstarszy pending thread dla numeru (registry) i wznawia graf (resume).
    `resume` wstrzykiwany (CI: fake; domyslnie resume_document).
    """
    app = FastAPI()

    @app.post("/whatsapp/inbound")
    def inbound(From: str = Form(...), Body: str = Form(...)) -> dict:
        decision = parse_decision(Body)
        if decision is None:
            return {"status": "ignored"}
        thread_id = registry.resolve_oldest(From)
        if thread_id is None:
            return {"status": "no_pending"}
        resume(graph, thread_id=thread_id, decision=decision)
        return {"status": "resumed", "decision": decision, "thread_id": thread_id}

    return app
```

- [ ] **Step 4: Verify pass** — `uv run pytest tests/unit/test_webhook.py -v` → PASS (5). `uv run pytest -q` → green (184 passed, 6 skipped). `uv run ruff check . && uv run ruff format --check .` → clean.

- [ ] **Step 5: Commit**
```bash
git add src/invoicer/webhook.py tests/unit/test_webhook.py
git commit -m "feat: FastAPI inbound webhook (/whatsapp/inbound) — parse TAK/NIE, resume oldest pending"
```

---

## Task 3: Orkiestracja `request_invoice_approval`

**Files:**
- Modify: `src/invoicer/runner.py`
- Test: `tests/unit/test_runner.py`

- [ ] **Step 1: Add failing test** — APPEND do `tests/unit/test_runner.py` (helpery `_graph(tmp_path)`, `_doc()` istnieją):
```python
def test_request_invoice_approval_registers_and_sends(tmp_path):
    from invoicer.adapters.stub_approval import StubApprovalChannel
    from invoicer.approvals import PendingApprovals
    from invoicer.runner import request_invoice_approval

    channel = StubApprovalChannel()
    registry = PendingApprovals(str(tmp_path / "p.sqlite"))
    payload = request_invoice_approval(
        _graph(tmp_path), channel, registry, _doc(), thread_id="w1", phone="whatsapp:+48500"
    )
    assert payload["number"] == "FV/1"
    assert channel.sent == [payload]  # request wyslany z payloadem (sprzedawca/NIP/kwota)
    assert registry.resolve_oldest("whatsapp:+48500") == "w1"  # zarejestrowany pending
```

- [ ] **Step 2: Run to verify it fails** — `uv run pytest tests/unit/test_runner.py -k request_invoice_approval -v` → FAIL (`ImportError: cannot import name 'request_invoice_approval'`).

- [ ] **Step 3: Implement** — APPEND do `src/invoicer/runner.py` (nie potrzeba nowych importów — używa istniejących `start_document`/`InvoiceDocument`; `channel`/`registry` są duck-typed):
```python
def request_invoice_approval(graph, channel, registry, document, *, thread_id, phone):
    """Uruchamia dokument do bramki, rejestruje pending i wysyla request akceptacji.

    Zwraca payload (do akceptacji) lub None gdy graf sie nie zatrzymal (brak interrupt).
    Odpowiedz czlowieka domyka webhook: registry.resolve_oldest(numer) -> resume_document.
    """
    payload = start_document(graph, document, thread_id=thread_id)
    if payload is None:
        return None
    registry.add(thread_id, phone)
    channel.request_approval(payload)
    return payload
```

- [ ] **Step 4: Verify pass** — `uv run pytest tests/unit/test_runner.py -v` → PASS (existing + new). `uv run pytest -q` → green (185 passed, 6 skipped). `uv run ruff check . && uv run ruff format --check .` → clean.

- [ ] **Step 5: Commit**
```bash
git add src/invoicer/runner.py tests/unit/test_runner.py
git commit -m "feat: request_invoice_approval — start, register pending, send approval request"
```

---

## Task 4: Lint + pełny suite (zielona baza)

- [ ] **Step 1: Ruff** — `cd /Users/mski/Developer/Invoicer && uv run ruff check . && uv run ruff format --check .` → clean.
- [ ] **Step 2: Pełny suite** — `uv run pytest -q` → **185 passed, 6 skipped** (zweryfikuj realne liczby: 176 + 3 pending + 5 webhook + 1 orkiestracja = 185).
- [ ] **Step 3: Commit porządkowy (jeśli ruff coś zmienił)** — `git add -A && git commit -m "chore: ruff clean, green suite (WhatsApp approval Plan B)" || echo "nic do commita"`.

---

## Uruchomienie na żywo (Ty — poza CI)

Złożenie dwukierunkowego flow (durable graf + rejestr w TEJ SAMEJ bazie, kanał Twilio, webhook):
```python
# serwer webhooka (np. src/run_webhook.py albo notebook):
from invoicer.runner import persistent_checkpointer
from invoicer.approvals import PendingApprovals
from invoicer.webhook import create_inbound_app
from invoicer.graph.build import build_invoice_graph
from invoicer.adapters.claude_extractor import ClaudeVisionExtractor
from invoicer.adapters.claude_reasoner import ClaudeExceptionReasoner
from invoicer.adapters.fakturownia import build_fakturownia_sink  # lub MockSubiektSink()
from invoicer.ledger import Ledger

DB = "approvals.sqlite"
graph = build_invoice_graph(
    extractor=ClaudeVisionExtractor(), reasoner=ClaudeExceptionReasoner(),
    sink=build_fakturownia_sink(), ledger=Ledger("ledger.jsonl"),
    checkpointer=persistent_checkpointer(DB),
)
registry = PendingApprovals(DB)
app = create_inbound_app(graph, registry)
# uruchom: uv run uvicorn run_webhook:app --port 8000
# wystaw publicznie: ngrok http 8000  -> ustaw URL w Twilio (Sandbox -> "When a message comes in")
# wysylka requestu: request_invoice_approval(graph, build_twilio_whatsapp_channel(), registry, doc, thread_id=..., phone=APPROVER_WHATSAPP_TO)
```
Odpowiedź „TAK/NIE" na WhatsAppie → Twilio → webhook → resume → księgowanie. (Wymaga Twilio sandbox + ngrok — setup po Twojej stronie.)

---

## Self-Review (wykonane przy pisaniu planu)

**Spec coverage (Plan B z §6):**
- `PendingApprovals` (numer→thread_id, FIFO) — spec §3.4 → Task 1 ✓ (FIFO via rowid zweryfikowane)
- Webhook `/whatsapp/inbound` (parse TAK/NIE, resume) — spec §3.5 → Task 2 ✓ (Form+TestClient zweryfikowane)
- Orkiestracja `request_invoice_approval` — spec §3.6 → Task 3 ✓
- Deps `fastapi`/`python-multipart`/`uvicorn` — spec §6 → Task 0 ✓
- Live e2e (Twilio+ngrok) — udokumentowane (sekcja „Uruchomienie na żywo"), nie CI.
- Walidacja podpisu Twilio — świadomie opcjonalna (spec §7); endpoint tylko wznawia istniejące pending.

**Placeholder scan:** brak TBD/TODO; pełny kod + komendy.

**Type consistency:** `PendingApprovals(db_path).add(thread_id, phone) -> None`, `.resolve_oldest(phone) -> str | None`; `parse_decision(body) -> str | None`; `create_inbound_app(graph, registry, *, resume=resume_document) -> FastAPI`; `request_invoice_approval(graph, channel, registry, document, *, thread_id, phone) -> dict | None`. Webhook woła `resume(graph, thread_id=..., decision=...)` zgodnie z `resume_document(graph, *, thread_id, decision)`. `channel.request_approval(payload)` (Plan A, bez thread_id). `registry.resolve_oldest(From)` — `From` Twilio = numer approvera = `phone` przekazany do `add`.

**Uwaga wykonawcza:** rejestr i checkpointer mogą dzielić tę samą bazę SQLite (osobne tabele) — w „Uruchomieniu na żywo" `DB` wspólne. `resume` wstrzykiwany do webhooka (testy: fake, bez realnego grafu). FIFO przez `rowid` (bez zegara — deterministyczne). Liczniki testów orientacyjne — zweryfikuj realne.
