# Invoicer — Plan 07: Streamlit HITL Demo Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Dodać wizualne demo (Streamlit) jako interfejs human-in-the-loop: wgraj fakturę PDF → przejdź przez graf → zobacz ekstrakcję/klasyfikację/flagi → zatwierdź/odrzuć → zobacz wynik księgowania. Klikalne demo do CV/rekrutera.

**Architecture:** Streamlit jest **reaktywny** (skrypt rerunuje się na każdą interakcję), a `process_document` blokuje na `decide(payload)`. Dlatego sterowanie grafem rozbijamy na dwie testowalne prymitywy w `runner.py`: `start_document` (invoke → pauza na `interrupt`, zwraca payload) i `resume_document` (wznów `Command(resume=...)`). Idealnie pasują do modelu Streamlit + checkpointer LangGraph (stan trzyma `thread_id` w `session_state`, graf utrwala się w `InMemorySaver` między rerunami). `process_document` (CLI) refaktoryzujemy, by delegował do tych prymitywów (DRY). Sam plik Streamlit to cienka prezentacja (nie testowana jednostkowo — runtime Streamlit), ale cała logika sterująca i helpery są w CI.

**Tech Stack:** Python 3.12, uv, **Streamlit**, LangGraph, Pydantic v2, pytest, ruff. Demo graf: offline (`StubExtractor`/`IdentityReasoner`) bez klucza, realny (`ClaudeVisionExtractor`/`ClaudeExceptionReasoner`) gdy `ANTHROPIC_API_KEY`.

**Spec:** `docs/superpowers/specs/2026-06-18-invoicer-design.md` — realizuje `HumanReview → StreamlitReview` (sek. 3, „demo rekruterskie").

**Stan wyjściowy:** Plany 01–06 scalone. `cli.py` ma `process_document(graph, document, *, thread_id, decide)`. `graph/build.py` `build_invoice_graph(*, extractor, ledger, sink, reasoner=None, clock=None, checkpointer=None)`. Adaptery: `StubExtractor`, `ClaudeVisionExtractor`, `IdentityReasoner`, `ClaudeExceptionReasoner`, `MockSubiektSink`, `Ledger`. UWAGA wersji: wynik `graph.invoke` to dict z kluczem `__interrupt__` (`result["__interrupt__"][0].value`); wznowienie `graph.invoke(Command(resume=...), config)` — zweryfikowane w P03/P04. 117 testów + 3 skipped, ruff czysty. Praca na `feat/plan-07-streamlit`. Komendy `uv run`. Importy na górze.

---

## File Structure

| Plik | Odpowiedzialność |
|------|------------------|
| `pyproject.toml` (MOD) | + `streamlit`. |
| `src/invoicer/runner.py` (NEW) | Prymitywy sterowania grafem (`start_document`, `resume_document`) + helpery demo (`document_from_upload`, `build_demo_graph`). |
| `src/invoicer/cli.py` (MOD) | `process_document` deleguje do `start_document`/`resume_document` (DRY). |
| `src/invoicer/ui/__init__.py` (NEW) | marker pakietu (lub brak — patrz Task 3). |
| `src/invoicer/ui/streamlit_app.py` (NEW) | Cienka prezentacja Streamlit (session_state, uploader, przyciski, wynik). |
| `tests/unit/test_runner.py` (NEW) | `start_document`/`resume_document` + `document_from_upload`/`build_demo_graph`. |

---

## Task 0: Gałąź + zależność Streamlit

- [ ] **Step 1** — `cd /Users/mski/Developer/Invoicer && git checkout main && git checkout -b feat/plan-07-streamlit`.
- [ ] **Step 2** — `uv add streamlit`. Expected: dodaje `streamlit` (+ zależności), aktualizuje `uv.lock`.
- [ ] **Step 3: Sanity** — `uv run python -c "import streamlit; print('ok')"` → `ok`.
- [ ] **Step 4: Suite + commit** — `uv run pytest -q` (117 passed, 3 skipped), `uv run ruff check .` (clean).
```bash
git add pyproject.toml uv.lock
git commit -m "build: add streamlit dependency"
```

---

## Task 1: Prymitywy sterowania grafem + refaktor process_document

**Files:**
- Create: `src/invoicer/runner.py`
- Modify: `src/invoicer/cli.py`
- Test: `tests/unit/test_runner.py`

- [ ] **Step 1: Write the failing test** — `tests/unit/test_runner.py`:
```python
from datetime import date, datetime
from decimal import Decimal

from invoicer.adapters.mock_subiekt import MockSubiektSink
from invoicer.adapters.stub_extractor import StubExtractor
from invoicer.graph.build import build_invoice_graph
from invoicer.ledger import Ledger
from invoicer.models import Invoice, InvoiceDocument, LineItem, Party
from invoicer.runner import resume_document, start_document


def _invoice() -> Invoice:
    line = LineItem(
        description="Usluga",
        quantity=Decimal("1"),
        unit_net=Decimal("1000.00"),
        vat_rate=Decimal("0.23"),
        net=Decimal("1000.00"),
        vat=Decimal("230.00"),
        gross=Decimal("1230.00"),
    )
    return Invoice(
        seller=Party(name="ACME", nip="5260001246", country="PL"),
        buyer=Party(name="Klient", country="PL"),
        number="FV/1",
        issue_date=date(2026, 6, 1),
        currency="PLN",
        lines=[line],
        total_net=Decimal("1000.00"),
        total_vat=Decimal("230.00"),
        total_gross=Decimal("1230.00"),
        extraction_confidence=0.95,
    )


def _doc() -> InvoiceDocument:
    return InvoiceDocument(
        sender="a@b.pl", received_at=datetime(2026, 6, 1), filename="x.pdf", content=b"%PDF"
    )


def _graph(tmp_path):
    return build_invoice_graph(
        extractor=StubExtractor(_invoice()),
        ledger=Ledger(tmp_path / "l.jsonl"),
        sink=MockSubiektSink(),
        clock=lambda: "2026-06-01T10:00:00",
    )


def test_start_document_returns_human_review_payload(tmp_path):
    payload = start_document(_graph(tmp_path), _doc(), thread_id="t1")
    assert payload["number"] == "FV/1"
    assert "treatment" in payload


def test_resume_document_approve_books(tmp_path):
    graph = _graph(tmp_path)
    start_document(graph, _doc(), thread_id="t2")
    final = resume_document(graph, thread_id="t2", decision="approve")
    assert final["booking"].booking_id == "MOCK-FV/1"


def test_resume_document_reject_does_not_book(tmp_path):
    graph = _graph(tmp_path)
    start_document(graph, _doc(), thread_id="t3")
    final = resume_document(graph, thread_id="t3", decision="reject")
    assert final.get("booking") is None
```

- [ ] **Step 2: Run to verify it fails** — `uv run pytest tests/unit/test_runner.py -v` → FAIL (`ModuleNotFoundError: No module named 'invoicer.runner'`).

- [ ] **Step 3: Implement `src/invoicer/runner.py`** (drivers only this task):
```python
from __future__ import annotations

from langgraph.types import Command

from invoicer.models import InvoiceDocument
from invoicer.state import InvoiceState


def start_document(graph, document: InvoiceDocument, *, thread_id: str) -> dict | None:
    """Uruchamia dokument w grafie do bramki human_review; zwraca payload interrupt (lub None)."""
    config = {"configurable": {"thread_id": thread_id}}
    result = graph.invoke({"document": document, "errors": []}, config)
    interrupts = result.get("__interrupt__")
    return interrupts[0].value if interrupts else None


def resume_document(graph, *, thread_id: str, decision: str) -> InvoiceState:
    """Wznawia graf po decyzji czlowieka (approve/reject/edit)."""
    config = {"configurable": {"thread_id": thread_id}}
    return graph.invoke(Command(resume=decision), config)
```

Refactor `src/invoicer/cli.py` so `process_document` delegates (DRY). Replace its body so the module reads:
```python
from __future__ import annotations

from collections.abc import Callable

from invoicer.models import InvoiceDocument
from invoicer.runner import resume_document, start_document
from invoicer.state import InvoiceState


def process_document(
    graph,
    document: InvoiceDocument,
    *,
    thread_id: str,
    decide: Callable[[dict], str],
) -> InvoiceState:
    """Przeprowadza jeden dokument przez graf z bramka czlowieka (CLI/sync).

    `decide(payload) -> "approve" | "reject"` dostaje podsumowanie z human_review.
    """
    payload = start_document(graph, document, thread_id=thread_id)
    if payload is None:  # graf nie zatrzymal sie (brak interrupt) — zwroc biezacy stan
        return graph.get_state({"configurable": {"thread_id": thread_id}}).values
    return resume_document(graph, thread_id=thread_id, decision=decide(payload))
```
(Remove the old `Command`/`langgraph` import from `cli.py` — now lives in `runner.py`.)

- [ ] **Step 4: Verify pass + no regressions** — `uv run pytest tests/unit/test_runner.py -v` → PASS (3). `uv run pytest tests/unit/test_cli.py -v` → existing 3 still PASS (process_document delegates, same behavior). `uv run pytest -q` → green (120 passed, 3 skipped). `uv run ruff check . && uv run ruff format --check .` → clean.

- [ ] **Step 5: Commit**
```bash
git add src/invoicer/runner.py src/invoicer/cli.py tests/unit/test_runner.py
git commit -m "feat: start_document/resume_document graph drivers; process_document delegates"
```

---

## Task 2: Helpery demo (upload → dokument, fabryka grafu)

**Files:**
- Modify: `src/invoicer/runner.py`
- Test: `tests/unit/test_runner.py`

- [ ] **Step 1: Add failing tests** — in `tests/unit/test_runner.py`, MERGE imports at top (ruff isort): add `from pathlib import Path` (stdlib) and extend the runner import to `from invoicer.runner import build_demo_graph, document_from_upload, resume_document, start_document`. Then APPEND:
```python
def test_document_from_upload_wraps_bytes():
    doc = document_from_upload("faktura.pdf", b"%PDF-1.4 x")
    assert doc.filename == "faktura.pdf"
    assert doc.content == b"%PDF-1.4 x"
    assert doc.sender  # niepuste (domyslny nadawca demo)


def test_build_demo_graph_returns_runnable_graph(tmp_path):
    graph = build_demo_graph(ledger_path=tmp_path / "demo.jsonl")
    assert hasattr(graph, "invoke")  # skompilowany graf LangGraph
```

- [ ] **Step 2: Run to verify it fails** — `uv run pytest tests/unit/test_runner.py -k "upload or demo" -v` → FAIL (`ImportError: cannot import name 'document_from_upload'`).

- [ ] **Step 3: Implement** — append to `src/invoicer/runner.py`. Add imports at top (stdlib `os`, `datetime`; `pathlib.Path`; first-party adapters/graph/models — keep isort order):
```python
import os
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

from invoicer.adapters.mock_subiekt import MockSubiektSink
from invoicer.adapters.stub_extractor import StubExtractor
from invoicer.adapters.stub_reasoner import IdentityReasoner
from invoicer.graph.build import build_invoice_graph
from invoicer.ledger import Ledger
from invoicer.models import Invoice, LineItem, Party
```
Append:
```python
def document_from_upload(filename: str, content: bytes, *, sender: str = "demo@upload") -> InvoiceDocument:
    """Owija wgrany plik w InvoiceDocument (received_at = teraz)."""
    return InvoiceDocument(
        sender=sender, received_at=datetime.now(UTC), filename=filename, content=content
    )


def _demo_invoice() -> Invoice:
    """Przykladowa faktura PL do trybu offline (gdy brak ANTHROPIC_API_KEY)."""
    line = LineItem(
        description="Usluga programistyczna (DEMO offline)",
        quantity=Decimal("1"),
        unit_net=Decimal("1000.00"),
        vat_rate=Decimal("0.23"),
        net=Decimal("1000.00"),
        vat=Decimal("230.00"),
        gross=Decimal("1230.00"),
    )
    return Invoice(
        seller=Party(name="ACME sp. z o.o.", nip="5260001246", country="PL"),
        buyer=Party(name="Klient sp. z o.o.", country="PL"),
        number="FV/DEMO/1",
        issue_date=datetime.now(UTC).date(),
        currency="PLN",
        lines=[line],
        total_net=Decimal("1000.00"),
        total_vat=Decimal("230.00"),
        total_gross=Decimal("1230.00"),
        extraction_confidence=0.95,
    )


def build_demo_graph(*, ledger_path: Path):
    """Buduje graf demo: realny Claude gdy ANTHROPIC_API_KEY, inaczej offline (stub)."""
    if os.getenv("ANTHROPIC_API_KEY"):
        from invoicer.adapters.claude_extractor import ClaudeVisionExtractor
        from invoicer.adapters.claude_reasoner import ClaudeExceptionReasoner

        extractor = ClaudeVisionExtractor()
        reasoner = ClaudeExceptionReasoner()
    else:
        extractor = StubExtractor(_demo_invoice())
        reasoner = IdentityReasoner()
    return build_invoice_graph(
        extractor=extractor, reasoner=reasoner, ledger=Ledger(ledger_path), sink=MockSubiektSink()
    )
```
(Note: `InvoiceDocument` is already imported in `runner.py` from Task 1.)

- [ ] **Step 4: Verify pass** — `uv run pytest tests/unit/test_runner.py -v` → PASS (5). `uv run pytest -q` → green (122 passed, 3 skipped). `uv run ruff check . && uv run ruff format --check .` → clean.

- [ ] **Step 5: Commit**
```bash
git add src/invoicer/runner.py tests/unit/test_runner.py
git commit -m "feat: demo helpers (document_from_upload, build_demo_graph offline/Claude)"
```

---

## Task 3: Aplikacja Streamlit (prezentacja)

**Files:**
- Create: `src/invoicer/ui/__init__.py` (empty)
- Create: `src/invoicer/ui/streamlit_app.py`

> Plik Streamlit to cienka prezentacja — NIE testowana jednostkowo (wymaga runtime Streamlit). Cała logika (start/resume/build) jest pokryta w `test_runner.py`. Importy `streamlit` są top-level (uruchamiane przez `streamlit run`).

- [ ] **Step 1: Create `src/invoicer/ui/__init__.py`** (empty).

- [ ] **Step 2: Implement `src/invoicer/ui/streamlit_app.py`**
```python
from __future__ import annotations

import tempfile
import uuid
from pathlib import Path

import streamlit as st

from invoicer.runner import (
    build_demo_graph,
    document_from_upload,
    resume_document,
    start_document,
)

st.set_page_config(page_title="Invoicer — demo", page_icon="🧾")
st.title("🧾 Invoicer — agentic invoice intake")
st.caption("Wgraj fakturę PDF → ekstrakcja (Claude) → walidacja PL → klasyfikacja → akceptacja człowieka → księgowanie.")

if "graph" not in st.session_state:
    ledger_path = Path(tempfile.gettempdir()) / "invoicer_demo_ledger.jsonl"
    st.session_state.graph = build_demo_graph(ledger_path=ledger_path)
    st.session_state.payload = None
    st.session_state.result = None
    st.session_state.thread_id = None

uploaded = st.file_uploader("Faktura (PDF)", type=["pdf"])

if st.button("Przetwórz", disabled=uploaded is None):
    doc = document_from_upload(uploaded.name, uploaded.getvalue())
    st.session_state.thread_id = str(uuid.uuid4())
    st.session_state.payload = start_document(
        st.session_state.graph, doc, thread_id=st.session_state.thread_id
    )
    st.session_state.result = None

payload = st.session_state.payload
if payload and st.session_state.result is None:
    st.subheader("Do akceptacji")
    cols = st.columns(2)
    cols[0].metric("Numer", payload["number"])
    cols[1].metric("Brutto", f"{payload['total_gross']} {payload['currency']}")
    st.write(f"**Sprzedawca:** {payload['seller']} ({payload['country']})")
    st.write(f"**Traktowanie:** `{payload['treatment']}` — {payload['rationale']}")
    if payload["flags"]:
        st.warning("Flagi: " + ", ".join(payload["flags"]))
    if payload["must_confirm"]:
        st.info("Do potwierdzenia: " + "; ".join(payload["must_confirm"]))
    decision = st.columns(2)
    if decision[0].button("✅ Zatwierdź"):
        st.session_state.result = resume_document(
            st.session_state.graph, thread_id=st.session_state.thread_id, decision="approve"
        )
    if decision[1].button("❌ Odrzuć"):
        st.session_state.result = resume_document(
            st.session_state.graph, thread_id=st.session_state.thread_id, decision="reject"
        )

result = st.session_state.result
if result is not None:
    booking = result.get("booking")
    if booking is not None:
        st.success(f"Zaksięgowano (mock): {booking.booking_id} → {booking.sink}")
    else:
        st.error("Odrzucono — nic nie zaksięgowano.")
```

- [ ] **Step 3: Sanity (nie uruchamiamy UI w CI)** — `uv run python -c "import ast; ast.parse(open('src/invoicer/ui/streamlit_app.py').read()); print('parse ok')"` → `parse ok` (sprawdza składnię bez runtime Streamlit). `uv run ruff check src/invoicer/ui/ && uv run ruff format --check src/invoicer/ui/` → clean.

- [ ] **Step 4: Commit**
```bash
git add src/invoicer/ui/__init__.py src/invoicer/ui/streamlit_app.py
git commit -m "feat: Streamlit HITL demo app (upload -> graph -> approve/reject -> booking)"
```

> **Uruchomienie (lokalnie, poza CI):** `uv run streamlit run src/invoicer/ui/streamlit_app.py`. Bez `ANTHROPIC_API_KEY` działa offline (kanon. faktura demo). Z kluczem — realna ekstrakcja wgranego PDF. Zrób screenshot/gif do README (sekcja demo).

---

## Task 4: Lint + pełny suite (zielona baza)

- [ ] **Step 1: Ruff** — `cd /Users/mski/Developer/Invoicer && uv run ruff check . && uv run ruff format --check .` → clean.
- [ ] **Step 2: Pełny suite** — `uv run pytest -q` → **122 passed, 3 skipped** (zweryfikuj realne liczby; P06 = 117+3 → +5 unit runner = 122).
- [ ] **Step 3: Commit porządkowy (jeśli ruff coś zmienił)** — `git add -A && git commit -m "chore: ruff clean, green suite (Plan 07 Streamlit done)" || echo "nic do commita"`.

---

## Self-Review (wykonane przy pisaniu planu)

**Spec coverage (Plan 07 = `StreamlitReview`, sek. 3):**
- Prymitywy sterowania grafem (`start_document`/`resume_document`) reaktywne pod Streamlit → Task 1 ✓
- `process_document` deleguje (DRY, brak duplikacji) → Task 1 ✓
- Helpery demo (`document_from_upload`, `build_demo_graph` offline/Claude) → Task 2 ✓
- Aplikacja Streamlit (upload → graf → approve/reject → wynik) → Task 3 ✓
- Logika w CI; UI jako prezentacja (świadoma granica) → Tasks 1–2 testowane, Task 3 tylko parse/ruff.

**Placeholder scan:** brak TBD/TODO; pełny kod + komendy. UI nie jest testowane jednostkowo (runtime Streamlit) — to świadoma granica, logika pokryta w `test_runner.py`.

**Type consistency:** `start_document(graph, document, *, thread_id) -> dict | None`, `resume_document(graph, *, thread_id, decision) -> InvoiceState`, `document_from_upload(filename, content, *, sender="demo@upload") -> InvoiceDocument`, `build_demo_graph(*, ledger_path) -> CompiledGraph`. `process_document` zachowuje sygnaturę (existing test_cli przechodzi). UWAGA wersji LangGraph: `result["__interrupt__"][0].value` + `Command(resume=...)` — zgodne z P03/P04 (zweryfikowane).

**Uwaga wykonawcza:** Streamlit + LangGraph interrupt to naturalne dopasowanie — graf pauzuje (`interrupt`, stan w checkpointerze pod `thread_id`), Streamlit rerunuje i pokazuje payload + przyciski, klik wznawia. `session_state` trzyma `graph`/`thread_id`/`payload`/`result`. `build_demo_graph` bez klucza buduje offline (stub) — test CI przechodzi deterministycznie (lazy klienci Claude i tak nie dotykają sieci przy konstrukcji).
