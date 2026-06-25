# Event-Driven Intake Trigger Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Zamienić trigger zaciągu faktur ze sztywnej godziny (cron 08:00) na reaktywny polling Gmaila co 5 min, z trwałą deduplikacją, tak że flow rusza, gdy tylko przyjdzie nowy mail z PDF.

**Architecture:** APScheduler `CronTrigger` → `IntervalTrigger(minutes=N)`. Polling wymaga idempotencji: nowy store `ProcessedDocuments` (SQLite, klucz `message_id:filename`) pamięta obsłużone załączniki. Polityka at-most-once — błąd faktury zapisuje `failed` + alert „manualna interwencja", bez ponawiania. Okno Gmaila `after/before` (dzień kalendarzowy) → `newer_than:2d` (nie gubi maili „zza północy").

**Tech Stack:** Python 3.12, APScheduler (`AsyncIOScheduler`/`IntervalTrigger`), SQLite (`sqlite3`), pydantic, pytest, ruff, uv. Gmail API (readonly), LangGraph.

**Spec:** [docs/superpowers/specs/2026-06-25-event-driven-intake-design.md](../specs/2026-06-25-event-driven-intake-design.md)

**Branch:** `feat/event-driven-intake` (już utworzona; spec już zacommitowany jako `17d4735`).

**Komendy projektu:**
- Pojedynczy test: `PYTHONPATH=src uv run pytest tests/unit/test_X.py::test_name -v`
- Pełny suite: `PYTHONPATH=src uv run pytest -q`
- Lint/format: `uv run ruff check . && uv run ruff format .`

---

## File Structure

| Plik | Odpowiedzialność | Akcja |
|------|------------------|-------|
| `src/invoicer/processed.py` | Trwały store dedup `ProcessedDocuments` + helper `document_key` | **Create** |
| `tests/unit/test_processed.py` | Testy store'a i `document_key` | **Create** |
| `src/invoicer/models.py` | `InvoiceDocument.message_id` (nowe pole) | Modify |
| `src/invoicer/adapters/gmail.py` | `_build_query` → `newer_than`; `fetch` ustawia `message_id` | Modify |
| `tests/unit/test_gmail.py` | Query `newer_than` + `message_id` na dokumencie | Modify |
| `src/invoicer/scheduler.py` | `build_scheduler` interwał; `run_daily_intake`→`run_intake` + dedup | Modify |
| `tests/unit/test_scheduler.py` | Interwał, pomijanie obsłużonych, alert bez ponawiania | **Rewrite** |
| `src/invoicer/app.py` | Settings/env/lifespan: interwał + wstrzyknięcie `processed` | Modify |
| `tests/unit/test_app.py` | `_settings` bez `intake_hour/minute/tz` | Modify |
| `.env.example` | `INTAKE_INTERVAL_MINUTES` | Modify |
| `fly.toml` | `[env]`: `INTAKE_HOUR/MINUTE/TZ` → `INTAKE_INTERVAL_MINUTES` | Modify |

**Świadomie nietknięte:** graf LangGraph, webhook, Streamlit, `runner.py` (`fetch_invoice_documents`/`request_invoice_approval` bez zmian), detektor faktury. Stare specy (2026-06-24) to zapis historyczny — nie przepisujemy.

---

## Task 0: Baseline

**Files:** brak (weryfikacja).

- [ ] **Step 1: Potwierdź gałąź i czysty baseline**

Run: `git branch --show-current` → oczekiwane: `feat/event-driven-intake`
Run: `PYTHONPATH=src uv run pytest -q`
Expected: PASS (cały suite zielony przed zmianami). Zanotuj liczbę testów (baseline).

---

## Task 1: `ProcessedDocuments` + `document_key` (store dedup)

**Files:**
- Modify: `src/invoicer/models.py` (dodanie pola `message_id`)
- Create: `src/invoicer/processed.py`
- Test: `tests/unit/test_processed.py`

- [ ] **Step 1: Dodaj pole `message_id` do `InvoiceDocument`**

W `src/invoicer/models.py`, klasa `InvoiceDocument` (obecnie kończy się na `subject: str = ""`), dodaj pole:

```python
class InvoiceDocument(BaseModel):
    """Surowy dokument wejsciowy (zalacznik e-mail) zanim nastapi ekstrakcja."""

    sender: str
    received_at: datetime
    filename: str
    content: bytes
    subject: str = ""
    message_id: str | None = None  # Gmail message id (klucz dedup); None dla upload/fixtur
```

- [ ] **Step 2: Napisz failing test dla store'a i `document_key`**

Utwórz `tests/unit/test_processed.py`:

```python
from __future__ import annotations

import hashlib
from datetime import datetime

from invoicer.models import InvoiceDocument
from invoicer.processed import ProcessedDocuments, document_key


def _doc(*, message_id=None, filename="f.pdf", content=b"%PDF") -> InvoiceDocument:
    return InvoiceDocument(
        sender="a@b.pl",
        received_at=datetime(2026, 6, 25),
        filename=filename,
        content=content,
        message_id=message_id,
    )


def test_seen_false_then_true_after_mark_done(tmp_path):
    store = ProcessedDocuments(str(tmp_path / "s.sqlite"))
    assert store.seen("k1") is False
    store.mark("k1", "done")
    assert store.seen("k1") is True


def test_seen_true_after_mark_failed(tmp_path):
    store = ProcessedDocuments(str(tmp_path / "s.sqlite"))
    store.mark("k2", "failed")
    assert store.seen("k2") is True  # failed tez liczy sie jako 'obsluzony' (at-most-once)


def test_mark_is_idempotent(tmp_path):
    store = ProcessedDocuments(str(tmp_path / "s.sqlite"))
    store.mark("k", "failed")
    store.mark("k", "done")  # INSERT OR REPLACE — bez bledu
    assert store.seen("k") is True


def test_persists_across_instances(tmp_path):
    path = str(tmp_path / "s.sqlite")
    ProcessedDocuments(path).mark("k", "done")
    assert ProcessedDocuments(path).seen("k") is True  # trwale (nowe polaczenie widzi wpis)


def test_document_key_uses_message_id_and_filename():
    a = document_key(_doc(message_id="m1", filename="a.pdf"))
    b = document_key(_doc(message_id="m1", filename="b.pdf"))
    assert a == "m1:a.pdf"
    assert a != b  # jeden mail, dwa zalaczniki = dwa rozne klucze


def test_document_key_falls_back_to_content_hash_without_message_id():
    k = document_key(_doc(message_id=None, filename="x.pdf", content=b"DANE"))
    assert k == f"{hashlib.sha256(b'DANE').hexdigest()}:x.pdf"
```

- [ ] **Step 3: Uruchom test — ma się wywalić**

Run: `PYTHONPATH=src uv run pytest tests/unit/test_processed.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'invoicer.processed'`.

- [ ] **Step 4: Zaimplementuj `processed.py`**

Utwórz `src/invoicer/processed.py`:

```python
from __future__ import annotations

import hashlib
import sqlite3
from datetime import UTC, datetime

from invoicer.models import InvoiceDocument


def document_key(document: InvoiceDocument) -> str:
    """Stabilny klucz deduplikacji dokumentu.

    Gmail: 'message_id:filename' (jeden mail z 2 PDF = 2 rozne klucze).
    Brak message_id (upload/fixtura): 'sha256(content):filename'.
    """
    head = document.message_id or hashlib.sha256(document.content).hexdigest()
    return f"{head}:{document.filename}"


class ProcessedDocuments:
    """Trwaly zbior obsluzonych dokumentow (idempotencja pollingu).

    Status done|failed — OBA znacza 'juz obsluzony, pomijaj' (at-most-once:
    przy bledzie NIE ponawiamy, by nie spamowac prosbami WhatsApp/alertami).
    check_same_thread=False: ten sam plik SQLite co checkpointer/PendingApprovals.
    """

    def __init__(self, db_path: str) -> None:
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.execute(
            "CREATE TABLE IF NOT EXISTS processed_documents ("
            "doc_key TEXT PRIMARY KEY, status TEXT NOT NULL, updated_at TEXT NOT NULL)"
        )
        self._conn.commit()

    def seen(self, doc_key: str) -> bool:
        """True jesli dokument byl juz obsluzony (dowolny status)."""
        row = self._conn.execute(
            "SELECT 1 FROM processed_documents WHERE doc_key = ? LIMIT 1", (doc_key,)
        ).fetchone()
        return row is not None

    def mark(self, doc_key: str, status: str) -> None:
        """Zapisuje dokument jako obsluzony ('done'|'failed'); idempotentne."""
        self._conn.execute(
            "INSERT OR REPLACE INTO processed_documents (doc_key, status, updated_at) "
            "VALUES (?, ?, ?)",
            (doc_key, status, datetime.now(UTC).isoformat()),
        )
        self._conn.commit()
```

- [ ] **Step 5: Uruchom testy — mają przejść**

Run: `PYTHONPATH=src uv run pytest tests/unit/test_processed.py -v`
Expected: PASS (6 testów).

- [ ] **Step 6: Commit**

```bash
git add src/invoicer/models.py src/invoicer/processed.py tests/unit/test_processed.py
git commit -m "feat(processed): ProcessedDocuments + document_key (dedup zaciagu)

InvoiceDocument.message_id (klucz dedup); store SQLite done|failed = 'obsluzony'.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 2: `GmailAdapter` — `newer_than:2d` + `message_id`

**Files:**
- Modify: `src/invoicer/adapters/gmail.py`
- Test: `tests/unit/test_gmail.py`

- [ ] **Step 1: Zaktualizuj testy query (failing)**

W `tests/unit/test_gmail.py`:

Usuń import `date` (linia 2: `from datetime import date`) — nie będzie już używany.

Zamień `test_build_query_filters_sender_pdf_and_day` (linie 20–22) na:

```python
def test_build_query_uses_newer_than_window():
    q = _build_query("a@b.pl")
    assert q == "from:a@b.pl newer_than:2d has:attachment filename:pdf"
```

Zamień `test_build_query_quotes_sender_with_spaces` (linie 64–69) na:

```python
def test_build_query_quotes_sender_with_spaces():
    q = _build_query("Vendor X <v@x.pl>")
    assert q == 'from:"Vendor X <v@x.pl>" newer_than:2d has:attachment filename:pdf'
```

Zamień `test_fetch_forwards_today_into_query` (linie 266–272) na:

```python
def test_fetch_query_uses_newer_than():
    service = _CapturingGmail()
    GmailAdapter(service).fetch("a@b.pl")
    assert service.msgs.queries[0] == "from:a@b.pl newer_than:2d has:attachment filename:pdf"
```

W `test_fetch_builds_invoice_document_from_pdf_attachment` (po linii 173 `assert doc.subject == "Faktura FV/1"`) dodaj:

```python
    assert doc.message_id == "m1"  # message_id z ref["id"] (klucz dedup)
```

- [ ] **Step 2: Uruchom testy — mają się wywalić**

Run: `PYTHONPATH=src uv run pytest tests/unit/test_gmail.py -v`
Expected: FAIL — query nadal `after/before`; `doc.message_id` to `None`.

- [ ] **Step 3: Zmień `_build_query` na `newer_than`**

W `src/invoicer/adapters/gmail.py` zamień funkcję `_build_query` (linie 12–17) na:

```python
def _build_query(sender: str, *, lookback_days: int = 2) -> str:
    """Zapytanie Gmail: PDF-y od nadawcy z ostatnich `lookback_days` dni (ruchome okno).

    newer_than zamiast after/before: polling przez polnoc nie gubi maili 'z wczoraj'.
    Trwala dedup (ProcessedDocuments) chroni przed podwojnym przetworzeniem.
    """
    token = f'"{sender}"' if (" " in sender or "<" in sender) else sender
    return f"from:{token} newer_than:{lookback_days}d has:attachment filename:pdf"
```

Zaktualizuj import na linii 5 (usuń nieużywane `date`, `timedelta`):

```python
from datetime import UTC, datetime
```

- [ ] **Step 4: `fetch` — sygnatura bez `today` + ustaw `message_id`**

W `src/invoicer/adapters/gmail.py`, metoda `GmailAdapter.fetch` (linie 73–106):

Zmień sygnaturę i budowę query (linie 73–75) z:

```python
    def fetch(self, sender: str, *, today: date | None = None) -> list[InvoiceDocument]:
        messages = self._service.users().messages()
        query = _build_query(sender, today=today or date.today())
```

na:

```python
    def fetch(self, sender: str, *, lookback_days: int = 2) -> list[InvoiceDocument]:
        messages = self._service.users().messages()
        query = _build_query(sender, lookback_days=lookback_days)
```

W bloku `docs.append(InvoiceDocument(...))` (linie 94–102) dodaj `message_id=ref["id"]`:

```python
                    docs.append(
                        InvoiceDocument(
                            sender=from_header,
                            subject=subject,
                            received_at=received_at,
                            filename=part.get("filename", "attachment.pdf"),
                            content=content,
                            message_id=ref["id"],
                        )
                    )
```

- [ ] **Step 5: Uruchom testy — mają przejść**

Run: `PYTHONPATH=src uv run pytest tests/unit/test_gmail.py -v`
Expected: PASS (wszystkie, w tym 3 zmienione + asercja `message_id`).

- [ ] **Step 6: Commit**

```bash
git add src/invoicer/adapters/gmail.py tests/unit/test_gmail.py
git commit -m "feat(gmail): newer_than:2d window + message_id na dokumencie

Ruchome okno 48h (polling przez polnoc nie gubi maili); message_id z ref[id]
jako klucz dedup. fetch: today -> lookback_days.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 3: `build_scheduler` interwał + `run_intake` (dedup)

**Files:**
- Modify: `src/invoicer/scheduler.py`
- Test (rewrite): `tests/unit/test_scheduler.py`

- [ ] **Step 1: Przepisz `test_scheduler.py` (failing)**

Zastąp CAŁĄ zawartość `tests/unit/test_scheduler.py` poniższym:

```python
from __future__ import annotations

from datetime import datetime, timedelta
from unittest.mock import MagicMock

import pytest

from invoicer.adapters.stub_approval import StubApprovalChannel
from invoicer.adapters.stub_detector import StubInvoiceDetector
from invoicer.models import InvoiceDocument
from invoicer.observability_status import PipelineCounters
from invoicer.processed import document_key
from invoicer.scheduler import build_scheduler, run_intake


class _FakeSource:
    def __init__(self, docs):
        self._docs = docs

    def fetch(self, sender):
        assert sender == "owner@example.com"
        return list(self._docs)


class _FakeProcessed:
    """In-memory namiastka ProcessedDocuments do testow jednostkowych."""

    def __init__(self, seen=()):
        self._seen = set(seen)
        self.marks: list[tuple[str, str]] = []

    def seen(self, key):
        return key in self._seen

    def mark(self, key, status):
        self.marks.append((key, status))
        self._seen.add(key)


def _doc(name: str) -> InvoiceDocument:
    return InvoiceDocument(
        sender="owner@example.com",
        received_at=datetime(2026, 6, 24),
        filename=name,
        content=b"%PDF-1.4",
    )


def test_run_intake_requests_approval_per_detected_invoice():
    docs = [_doc("a.pdf"), _doc("b.pdf")]
    channel = StubApprovalChannel()
    registry = MagicMock()
    counters = PipelineCounters()
    processed = _FakeProcessed()
    payload = {"number": "FV/1", "total_gross": "1230.00", "currency": "PLN"}

    def fake_request(graph_, channel_, registry_, document, *, thread_id, phone):
        channel_.request_approval(payload)
        registry_.add(thread_id, phone)
        return payload

    run_intake(
        MagicMock(), channel, registry, _FakeSource(docs),
        StubInvoiceDetector(result=True),
        sender="owner@example.com", phone="whatsapp:+48111",
        counters=counters, processed=processed, request_fn=fake_request,
    )
    assert len(channel.sent) == 2
    assert counters.processed == 2
    assert counters.failed == 0
    assert registry.add.call_count == 2
    assert processed.marks == [
        (document_key(docs[0]), "done"),
        (document_key(docs[1]), "done"),
    ]


def test_run_intake_skips_already_processed():
    docs = [_doc("a.pdf"), _doc("b.pdf")]
    processed = _FakeProcessed(seen={document_key(docs[0])})  # a.pdf juz obsluzony
    channel = StubApprovalChannel()
    counters = PipelineCounters()
    calls: list[str] = []

    def request_fn(graph, channel_, registry, document, *, thread_id, phone):
        calls.append(document.filename)
        channel_.request_approval({"x": document.filename})
        return {"x": document.filename}

    run_intake(
        MagicMock(), channel, MagicMock(), _FakeSource(docs),
        StubInvoiceDetector(result=True),
        sender="owner@example.com", phone="whatsapp:+48111",
        counters=counters, processed=processed, request_fn=request_fn,
    )
    assert calls == ["b.pdf"]  # a.pdf pominiety (idempotencja)
    assert counters.processed == 1
    assert processed.marks == [(document_key(docs[1]), "done")]


def test_run_intake_marks_failed_and_alerts_without_retry():
    docs = [_doc("a.pdf"), _doc("b.pdf"), _doc("c.pdf")]
    channel = StubApprovalChannel()
    counters = PipelineCounters()
    processed = _FakeProcessed()
    alerts: list[tuple[str, str]] = []

    def request_fn(graph, channel_, registry, document, *, thread_id, phone):
        if document.filename == "b.pdf":
            raise RuntimeError("ekstrakcja padla")
        channel_.request_approval({"x": document.filename})
        return {"x": document.filename}

    run_intake(
        MagicMock(), channel, MagicMock(), _FakeSource(docs),
        StubInvoiceDetector(result=True),
        sender="owner@example.com", phone="whatsapp:+48111",
        counters=counters, processed=processed, request_fn=request_fn,
        alert=lambda ctx, reason: alerts.append((ctx, reason)),
    )
    assert [m["x"] for m in channel.sent] == ["a.pdf", "c.pdf"]
    assert counters.processed == 2
    assert counters.failed == 1
    # b.pdf zapisany jako 'failed' (NIE bedzie ponowiony) i zaalarmowany raz
    assert (document_key(docs[1]), "failed") in processed.marks
    assert len(alerts) == 1
    assert alerts[0][0] == "b.pdf"
    assert "manualnej interwencji" in alerts[0][1]
    assert "ekstrakcja padla" in alerts[0][1]


class _BoomSource:
    def fetch(self, sender):
        raise RuntimeError("token Gmaila wygasl")


def test_run_intake_alerts_when_fetch_fails():
    # blad zaciagu (poza petla per-faktura) MUSI zaalarmowac i podniesc wyjatek
    alerts: list[tuple[str, str]] = []
    with pytest.raises(RuntimeError, match="token Gmaila wygasl"):
        run_intake(
            MagicMock(), StubApprovalChannel(), MagicMock(), _BoomSource(),
            StubInvoiceDetector(result=True),
            sender="owner@example.com", phone="whatsapp:+48111",
            counters=PipelineCounters(), processed=_FakeProcessed(),
            alert=lambda ctx, reason: alerts.append((ctx, reason)),
        )
    assert alerts == [("intake", "token Gmaila wygasl")]


def test_build_scheduler_adds_interval_job():
    sched = build_scheduler(lambda: None, interval_minutes=5)
    jobs = sched.get_jobs()
    assert len(jobs) == 1
    job = jobs[0]
    assert job.id == "intake"
    assert job.max_instances == 1
    assert job.coalesce is True
    assert job.trigger.interval == timedelta(minutes=5)  # IntervalTrigger, nie cron
```

- [ ] **Step 2: Uruchom testy — mają się wywalić**

Run: `PYTHONPATH=src uv run pytest tests/unit/test_scheduler.py -v`
Expected: FAIL — `ImportError: cannot import name 'run_intake'` (oraz brak `interval_minutes`).

- [ ] **Step 3: Zaimplementuj zmiany w `scheduler.py`**

W `src/invoicer/scheduler.py`:

Zamień importy triggera (linie 7–8):

```python
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger
```

Dodaj import store'a (po linii `from invoicer.ports import ...`):

```python
from invoicer.processed import ProcessedDocuments, document_key
```

Zamień `run_daily_intake` (linie 17–53) na `run_intake`:

```python
def run_intake(
    graph: Any,
    channel: Any,
    registry: Any,
    source: EmailSource,
    detector: InvoiceDetector,
    *,
    sender: str,
    phone: str,
    counters: PipelineCounters,
    processed: ProcessedDocuments,
    request_fn: Callable[..., dict | None] = request_invoice_approval,
    alert: Callable[[str, str], None] = lambda *_: None,
) -> None:
    """Reaktywny zaciag (polling): Gmail -> detekcja -> per-faktura request akceptacji.

    Idempotentny: pomija dokumenty juz obsluzone (ProcessedDocuments) — ten sam mail
    nie generuje powtornych prosb co cykl. At-most-once: blad faktury -> mark 'failed'
    + alert 'manualna interwencja' (BEZ ponawiania). thread_id generowany lokalnie.
    """
    import uuid

    try:
        docs = fetch_invoice_documents(source, detector, sender)
    except Exception as exc:  # noqa: BLE001 - blad zaciagu (np. wygasly token Gmaila) musi zaalarmowac
        _logger.exception("intake: pobranie faktur nie powiodlo sie")
        alert("intake", str(exc))
        raise
    _logger.info("intake start: %d faktur (przed dedup)", len(docs))
    for doc in docs:
        key = document_key(doc)
        if processed.seen(key):
            continue
        thread_id = f"intake-{uuid.uuid4()}"
        try:
            request_fn(graph, channel, registry, doc, thread_id=thread_id, phone=phone)
            processed.mark(key, "done")
            counters.incr_processed()
        except Exception as exc:  # noqa: BLE001 - at-most-once: zapisz failed, NIE ponawiaj, zaalarmuj
            processed.mark(key, "failed")
            counters.incr_failed()
            _logger.exception("intake: faktura %s nie przeszla (manualna interwencja)", doc.filename)
            alert(doc.filename, f"wymaga manualnej interwencji: {exc}")
    _logger.info("intake done: processed=%d failed=%d", counters.processed, counters.failed)
```

Zamień `build_scheduler` (linie 56–69) na:

```python
def build_scheduler(job: Callable[[], None], *, interval_minutes: int) -> AsyncIOScheduler:
    """Buduje AsyncIOScheduler z jednym interwalowym jobem; coalesce + max_instances=1.

    coalesce + max_instances=1: gdy przebieg przeciagnie sie ponad interwal,
    kolejny tick jest pominiety (nie nakladaja sie rownolegle).
    """
    sched = AsyncIOScheduler()
    sched.add_job(
        job,
        IntervalTrigger(minutes=interval_minutes),
        id="intake",
        max_instances=1,
        coalesce=True,
        misfire_grace_time=60,
    )
    return sched
```

- [ ] **Step 4: Uruchom testy — mają przejść**

Run: `PYTHONPATH=src uv run pytest tests/unit/test_scheduler.py -v`
Expected: PASS (6 testów).

- [ ] **Step 5: Commit**

```bash
git add src/invoicer/scheduler.py tests/unit/test_scheduler.py
git commit -m "feat(scheduler): IntervalTrigger + run_intake z dedup (at-most-once)

CronTrigger(hour,minute) -> IntervalTrigger(minutes); run_daily_intake -> run_intake
pomija obsluzone (ProcessedDocuments), blad -> mark failed + alert, bez ponawiania.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 4: `app.py` — konfiguracja interwału + wstrzyknięcie `processed`

**Files:**
- Modify: `src/invoicer/app.py`
- Modify: `tests/unit/test_app.py`
- Modify: `.env.example`
- Modify: `fly.toml`

- [ ] **Step 1: Zaktualizuj `test_app.py` (failing)**

W `tests/unit/test_app.py`, funkcja `_settings` (linie 8–18), usuń trzy linie `intake_*`:

```python
def _settings(tmp_path) -> AppSettings:
    return AppSettings(
        approver_phone="whatsapp:+48111",
        gmail_sender="owner@example.com",
        data_dir=tmp_path,
        # tryb testowy: bez realnych adapterow / scheduler nie startuje
        test_mode=True,
    )
```

- [ ] **Step 2: Uruchom test — ma się wywalić**

Run: `PYTHONPATH=src uv run pytest tests/unit/test_app.py -v`
Expected: FAIL — `TypeError: ... unexpected keyword argument` zniknie, ale import/konstrukcja nadal odwołuje się do starego `build_scheduler(hour=...)`; po Step 1 test może paść też na `AppSettings` mającym wciąż stare pola z wartościami domyślnymi (przejdzie dopiero po Step 3). Zanotuj błąd.

> Uwaga: jeśli po Step 1 test PRZECHODZI (bo stare pola mają defaulty), to oczekiwane — właściwy failing test dla interwału jest pośredni; kluczowe jest, by po Step 3 cały suite był zielony. Przejdź do Step 3.

- [ ] **Step 3: Zaktualizuj `app.py`**

W `src/invoicer/app.py`:

Zmień import (linia 23):

```python
from invoicer.scheduler import build_scheduler, run_intake
```

Dodaj import store'a (po linii 22 `from invoicer.runner import ...`):

```python
from invoicer.processed import ProcessedDocuments
```

Zamień pola `AppSettings` (linie 32–34) `intake_hour/intake_minute/intake_tz` na jedno:

```python
@dataclass
class AppSettings:
    approver_phone: str
    gmail_sender: str
    intake_interval_minutes: int = 5
    data_dir: Path = Path("/data")
    test_mode: bool = False  # True w testach: stuby + scheduler nie startuje
```

Zamień odczyt env w `_settings_from_env` (linie 43–45) na:

```python
def _settings_from_env() -> AppSettings:
    return AppSettings(
        approver_phone=os.environ["APPROVER_WHATSAPP_TO"],
        gmail_sender=os.environ["GMAIL_SENDER_FILTER"],
        intake_interval_minutes=int(os.getenv("INTAKE_INTERVAL_MINUTES", "5")),
        data_dir=Path(os.getenv("INVOICER_DATA_DIR", "/data")),
    )
```

Dodaj utworzenie store'a po `registry = PendingApprovals(db_path)` (linia 104):

```python
    registry = PendingApprovals(db_path)
    processed = ProcessedDocuments(db_path)
```

W `_job` (linie 134–148) zamień wywołanie `run_daily_intake(` na `run_intake(` i dodaj `processed=processed`:

```python
        def _job() -> None:
            service = gmail_service_from_token(settings.data_dir / "token.json")
            run_intake(
                graph,
                channel,
                registry,
                GmailAdapter(service),
                ClaudeInvoiceDetector(),
                sender=settings.gmail_sender,
                phone=settings.approver_phone,
                counters=counters,
                processed=processed,
                alert=lambda ctx, reason: send_failure_alert(
                    channel, format_failure_alert(ctx, reason)
                ),
            )
```

Zamień wywołanie `build_scheduler` (linie 150–155) na:

```python
        scheduler = build_scheduler(
            _job,
            interval_minutes=settings.intake_interval_minutes,
        )
```

- [ ] **Step 4: Uruchom testy `test_app.py` — mają przejść**

Run: `PYTHONPATH=src uv run pytest tests/unit/test_app.py -v`
Expected: PASS (5 testów).

- [ ] **Step 5: Zaktualizuj `.env.example`**

W `.env.example` dodaj po linii `GMAIL_SENDER_FILTER=` (linia 3):

```
# Czestotliwosc pollingu Gmaila w minutach (trigger zaciagu; domyslnie 5)
INTAKE_INTERVAL_MINUTES=5
```

- [ ] **Step 6: Zaktualizuj `fly.toml`**

> **UWAGA — entanglement:** `fly.toml` ma już niezacommitowane zmiany użytkownika (regeneracja `fly launch`: nazwa appki, region `ams`). Edytuj TYLKO blok `[env]`; przy commicie patrz Step 8.

W `fly.toml`, w sekcji `[env]`, usuń trzy linie `INTAKE_HOUR/INTAKE_MINUTE/INTAKE_TZ` i dodaj jedną:

```toml
[env]
  INTAKE_INTERVAL_MINUTES = '5'
  INVOICER_DATA_DIR = '/data'
  PORT = '8080'
```

- [ ] **Step 7: Uruchom pełny suite**

Run: `PYTHONPATH=src uv run pytest -q`
Expected: PASS (baseline z Task 0 + nowe testy `test_processed.py`; zero regresji).

- [ ] **Step 8: Commit (uwaga na `fly.toml`)**

Najpierw zacommituj kod + env (bez `fly.toml`):

```bash
git add src/invoicer/app.py tests/unit/test_app.py .env.example
git commit -m "feat(app): INTAKE_INTERVAL_MINUTES + wstrzykniecie ProcessedDocuments

AppSettings: intake_hour/minute/tz -> intake_interval_minutes (default 5);
run_intake z processed=ProcessedDocuments(db_path).

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

`fly.toml` zawiera też niezwiązane zmiany deploymentowe użytkownika — **NIE commituj automatycznie**. Pokaż diff (`git diff fly.toml`) i zapytaj użytkownika, czy zacommitować całość (env + jego zmiany `fly launch`) jednym commitem, czy zostawić `fly.toml` w working tree do jego własnego commita.

---

## Task 5: Lint + zielony suite (finał)

**Files:** brak nowych.

- [ ] **Step 1: Lint + format**

Run: `uv run ruff check . && uv run ruff format .`
Expected: brak błędów; jeśli `ruff format` coś zmieni — `git add -A && git commit -m "chore(review): ruff format"`.

- [ ] **Step 2: Pełny suite**

Run: `PYTHONPATH=src uv run pytest -q`
Expected: PASS, zero fails.

- [ ] **Step 3: Weryfikacja braku martwych referencji**

Run: `grep -rn "run_daily_intake\|INTAKE_HOUR\|INTAKE_MINUTE\|INTAKE_TZ\|intake_hour\|intake_minute\|intake_tz" --include="*.py" src tests`
Expected: brak wyników (poza ewentualnymi historycznymi specami w `docs/`, których nie ruszamy).

- [ ] **Step 4: (Opcjonalnie) smoke `scripts/run_flow_now.py`**

`scripts/run_flow_now.py` używa `GmailAdapter`/`InvoiceDocument` bezpośrednio (nie `run_daily_intake`), więc zmiana sygnatury `fetch` (`today`→`lookback_days`, oba keyword-only z defaultem) go nie psuje. Jeśli skrypt wywołuje `fetch(..., today=...)` — popraw na `fetch(...)` lub `lookback_days=...`. Sprawdź: `grep -n "today=" scripts/run_flow_now.py`.

---

## Self-Review (autor planu)

**Spec coverage:**
- §3.1 IntervalTrigger → Task 3. ✓
- §3.2 ProcessedDocuments + document_key → Task 1. ✓
- §3.3 InvoiceDocument.message_id + GmailAdapter → Task 1 (pole) + Task 2 (adapter). ✓
- §3.4 newer_than:2d → Task 2. ✓
- §3.5 run_intake (dedup + alert) → Task 3. ✓
- §3.6 app.py settings/env/lifespan → Task 4. ✓
- §5 testy (test_processed/scheduler/gmail) → Task 1/2/3. ✓ (+ test_app.py — wykryte poza specem, ujęte w Task 4.)
- §2 .env.example + fly.toml → Task 4. ✓

**Type/sygnatury consistency:** `document_key(document)->str`, `ProcessedDocuments.seen(key)->bool`/`.mark(key,status)`, `run_intake(..., processed=...)`, `build_scheduler(job, *, interval_minutes)` — spójne między Task 1/3/4 i testami. ✓

**Placeholders:** brak TBD/TODO; każdy krok z pełnym kodem. ✓

**Odchylenia od specu (świadome):** `test_app.py` (aktualizacja `_settings`) nie był wymieniony w specu — dodany do Task 4 (konieczny, bo używa usuwanych pól).
