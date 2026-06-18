# Invoicer — Plan 03: LangGraph Graph + CLI HITL Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Spiąć dotychczasowe warstwy w działający graf stanów LangGraph: `extract → validate → classify → human_review (interrupt, CLI) → book`, z bramką akceptacji człowieka, checkpointingiem i wymiennym ekstraktorem.

**Architecture:** Rdzeń to `StateGraph` nad `InvoiceState` (TypedDict). Węzły są budowane przez fabryki domknięte na wstrzykniętych zależnościach (`extractor`, `ledger`, `sink`, `clock`) — dzięki temu graf jest testowalny deterministycznie. Ekstrakcja idzie przez port `InvoiceExtractor`; w tym planie używamy `StubExtractor` (zwraca z góry ustaloną `Invoice`), więc CI nie dotyka API. Human-in-the-loop to natywny `interrupt()` + `Command(resume=...)` z checkpointerem `InMemorySaver`. Realny `ClaudeVisionExtractor` i `reason_exception` (LLM) są celowo w Planie 04.

**Tech Stack:** Python 3.12, uv, **LangGraph** (`StateGraph`, `interrupt`, `InMemorySaver`), Pydantic v2, pytest, ruff. Bez `langchain-anthropic` w tym planie (dochodzi w Planie 04 z realnym ekstraktorem).

**Spec:** `docs/superpowers/specs/2026-06-18-invoicer-design.md` — realizuje Kamień 3 oraz sekcję 4 (graf, węzły, krawędzie, stan), część sekcji 5 (`Classification`) i 7 (HITL, idempotencja).

**Stan wyjściowy:** Plany 01–02 scalone. Istnieją: `models.py` (`Invoice`, `Party`, `LineItem`, `InvoiceDocument`, `Check`, `CheckStatus`, `ValidationResult`), `booking.py` (`BookingPayload`, `BookingResult`, `invoice_to_booking_payload`), `ports.py` (`EmailSource`, `AccountingSink`), `ledger.py` (`Ledger`, `LedgerEntry`), `validation.py` (`validate_invoice(invoice, ledger=None)`), adaptery (`FixtureSource`, `MockSubiektSink`). 46 testów zielonych, ruff E/F/I/UP/B + line-length 100. Praca na `feat/plan-03-graph-hitl`. Komendy `uv run`. Importy na górze plików.

---

## File Structure

| Plik | Odpowiedzialność |
|------|------------------|
| `pyproject.toml` (MOD) | + zależność `langgraph`. |
| `src/invoicer/models.py` (MOD) | + `CountryBucket`, `TaxTreatment` (StrEnum), `Classification`. |
| `src/invoicer/ports.py` (MOD) | + Protocol `InvoiceExtractor`. |
| `src/invoicer/state.py` (NEW) | `InvoiceState` (TypedDict) — stan grafu. |
| `src/invoicer/adapters/stub_extractor.py` (NEW) | `StubExtractor` — deterministyczny `InvoiceExtractor` do testów/demo. |
| `src/invoicer/graph/__init__.py` (NEW) | Marker pakietu. |
| `src/invoicer/graph/nodes.py` (NEW) | Fabryki węzłów: extract, validate, classify, human_review, book + routing. |
| `src/invoicer/graph/build.py` (NEW) | `build_invoice_graph(...)` — montaż `StateGraph`. |
| `src/invoicer/cli.py` (NEW) | `process_document(...)` — driver: invoke → obsłuż interrupt → resume. |
| `tests/unit/test_classification.py` (NEW) | `Classification` + enumy. |
| `tests/unit/test_state.py` (NEW) | `InvoiceState` kształt + `InvoiceExtractor`/`StubExtractor`. |
| `tests/unit/test_nodes.py` (NEW) | węzły extract/validate/classify/book. |
| `tests/unit/test_graph.py` (NEW) | e2e: approve→book, reject→no book. |
| `tests/unit/test_cli.py` (NEW) | driver z wstrzykniętą decyzją. |

**Wzorzec wstrzykiwania:** węzły zależne od I/O (extractor, ledger, sink, zegar) powstają z fabryk `make_*_node(dep)`; `build_invoice_graph` domyka je. Klasyfikacja w tym planie jest **deterministyczna** (kraj PL vs UE vs poza-UE, domyślne traktowanie) — bogate rozumowanie LLM (`reason_exception`) i realny ekstraktor wchodzą w Planie 04.

---

## Task 0: Gałąź + zależność LangGraph

- [ ] **Step 1: Gałąź**

Run:
```bash
cd /Users/mski/Developer/Invoicer && git checkout master && git checkout -b feat/plan-03-graph-hitl
```

- [ ] **Step 2: Dodaj LangGraph**

Run: `cd /Users/mski/Developer/Invoicer && uv add langgraph`
Expected: dodaje `langgraph` do `[project].dependencies` w `pyproject.toml`, aktualizuje `uv.lock`, instaluje (langgraph + langchain-core + zależności).

- [ ] **Step 3: Sanity import**

Run: `uv run python -c "from langgraph.graph import StateGraph, START, END; from langgraph.types import interrupt, Command; from langgraph.checkpoint.memory import InMemorySaver; print('ok')"`
Expected: `ok`.

- [ ] **Step 4: Commit**
```bash
git add pyproject.toml uv.lock
git commit -m "build: add langgraph dependency"
```

---

## Task 1: Modele klasyfikacji

**Files:**
- Modify: `src/invoicer/models.py`
- Test: `tests/unit/test_classification.py`

- [ ] **Step 1: Write the failing test** — `tests/unit/test_classification.py`:
```python
from invoicer.models import Classification, CountryBucket, TaxTreatment


def test_treatment_and_bucket_are_string_enums():
    assert TaxTreatment.IMPORT_USLUG == "import_uslug"
    assert CountryBucket.POZA_UE == "poza_UE"


def test_classification_defaults():
    c = Classification(treatment=TaxTreatment.KRAJOWA, country_bucket=CountryBucket.PL)
    assert c.confidence == 1.0
    assert c.rationale_pl == ""
    assert c.human_must_confirm == []
    assert c.currency_note == ""


def test_classification_full():
    c = Classification(
        treatment=TaxTreatment.IMPORT_USLUG,
        country_bucket=CountryBucket.POZA_UE,
        confidence=0.7,
        rationale_pl="UK bez VAT",
        human_must_confirm=["usluga czy towar?"],
        currency_note="GBP -> NBP",
    )
    assert c.treatment == TaxTreatment.IMPORT_USLUG
    assert c.human_must_confirm == ["usluga czy towar?"]
```

- [ ] **Step 2: Run to verify it fails** — `uv run pytest tests/unit/test_classification.py -v` → FAIL (`ImportError: cannot import name 'Classification'`).

- [ ] **Step 3: Modify `src/invoicer/models.py`**

Change the pydantic import (currently `from pydantic import BaseModel`) to:
```python
from pydantic import BaseModel, Field
```
Append these classes at the END of the file:
```python
class CountryBucket(StrEnum):
    PL = "PL"
    UE = "UE"
    POZA_UE = "poza_UE"


class TaxTreatment(StrEnum):
    KRAJOWA = "krajowa"
    IMPORT_USLUG = "import_uslug"
    IMPORT_TOWAROW = "import_towarow"
    WNT = "wnt"
    INNE = "inne"


class Classification(BaseModel):
    """Proponowane traktowanie podatkowe faktury (potwierdza czlowiek)."""

    treatment: TaxTreatment
    country_bucket: CountryBucket
    confidence: float = 1.0
    rationale_pl: str = ""
    human_must_confirm: list[str] = Field(default_factory=list)
    currency_note: str = ""
```

- [ ] **Step 4: Verify pass** — `uv run pytest tests/unit/test_classification.py -v` → PASS (3). Then `uv run pytest -q` → all prior green. `uv run ruff check . && uv run ruff format --check .` → clean.

- [ ] **Step 5: Commit**
```bash
git add src/invoicer/models.py tests/unit/test_classification.py
git commit -m "feat: Classification model + CountryBucket/TaxTreatment enums"
```

---

## Task 2: Stan grafu + port ekstraktora + StubExtractor

**Files:**
- Create: `src/invoicer/state.py`
- Modify: `src/invoicer/ports.py`
- Create: `src/invoicer/adapters/stub_extractor.py`
- Test: `tests/unit/test_state.py`

- [ ] **Step 1: Write the failing test** — `tests/unit/test_state.py`:
```python
from datetime import date, datetime
from decimal import Decimal

from invoicer.adapters.stub_extractor import StubExtractor
from invoicer.models import Invoice, InvoiceDocument, LineItem, Party
from invoicer.ports import InvoiceExtractor
from invoicer.state import InvoiceState


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
    )


def _doc() -> InvoiceDocument:
    return InvoiceDocument(
        sender="a@b.pl",
        received_at=datetime(2026, 6, 1, 10, 0, 0),
        filename="x.pdf",
        content=b"%PDF",
    )


def test_invoice_state_accepts_partial_dict():
    state: InvoiceState = {"document": _doc(), "errors": []}
    assert state["document"].filename == "x.pdf"


def test_stub_extractor_satisfies_protocol():
    assert isinstance(StubExtractor(_invoice()), InvoiceExtractor)


def test_stub_extractor_returns_independent_copy():
    inv = _invoice()
    extractor = StubExtractor(inv)
    out = extractor.extract(_doc())
    out.seller.name = "ZMIENIONE"
    assert inv.seller.name == "ACME"  # stub zwraca niezalezna kopie
    assert out.number == "FV/1"
```

- [ ] **Step 2: Run to verify it fails** — `uv run pytest tests/unit/test_state.py -v` → FAIL (`ModuleNotFoundError: No module named 'invoicer.state'`).

- [ ] **Step 3: Implement**

Create `src/invoicer/state.py`:
```python
from __future__ import annotations

import operator
from typing import Annotated

from typing_extensions import TypedDict

from invoicer.booking import BookingResult
from invoicer.models import Classification, Invoice, InvoiceDocument, ValidationResult


class InvoiceState(TypedDict, total=False):
    """Stan przeplywajacy przez graf. total=False -> wezly zwracaja czesciowe aktualizacje."""

    document: InvoiceDocument
    invoice: Invoice | None
    validation: ValidationResult | None
    classification: Classification | None
    human_decision: str | None  # "approve" | "reject" | "edit"
    booking: BookingResult | None
    extract_attempts: int
    errors: Annotated[list[str], operator.add]
```

Add `Invoice` to the import in `src/invoicer/ports.py` (it currently imports `from invoicer.models import InvoiceDocument`):
```python
from invoicer.models import Invoice, InvoiceDocument
```
Append this Protocol to `src/invoicer/ports.py`:
```python
@runtime_checkable
class InvoiceExtractor(Protocol):
    """Wyciaga ustrukturyzowana Invoice z surowego dokumentu (PDF/skan)."""

    def extract(self, document: InvoiceDocument) -> Invoice: ...
```

Create `src/invoicer/adapters/stub_extractor.py`:
```python
from __future__ import annotations

from invoicer.models import Invoice, InvoiceDocument


class StubExtractor:
    """Deterministyczny InvoiceExtractor do testow/demo offline.

    Zwraca z gory ustalona Invoice (niezalezna kopie), bez kontaktu z LLM.
    Realny ClaudeVisionExtractor dochodzi w Planie 04.
    """

    def __init__(self, invoice: Invoice) -> None:
        self._invoice = invoice

    def extract(self, document: InvoiceDocument) -> Invoice:
        return self._invoice.model_copy(deep=True)
```

- [ ] **Step 4: Verify pass** — `uv run pytest tests/unit/test_state.py -v` → PASS (3). `uv run pytest -q` → all green. `uv run ruff check . && uv run ruff format --check .` → clean.

- [ ] **Step 5: Commit**
```bash
git add src/invoicer/state.py src/invoicer/ports.py src/invoicer/adapters/stub_extractor.py tests/unit/test_state.py
git commit -m "feat: InvoiceState + InvoiceExtractor port + StubExtractor"
```

---

## Task 3: Węzły extract i validate

**Files:**
- Create: `src/invoicer/graph/__init__.py`
- Create: `src/invoicer/graph/nodes.py`
- Test: `tests/unit/test_nodes.py`

- [ ] **Step 1: Write the failing test** — `tests/unit/test_nodes.py`:
```python
from datetime import date, datetime
from decimal import Decimal

from invoicer.adapters.stub_extractor import StubExtractor
from invoicer.graph.nodes import make_extract_node, make_validate_node
from invoicer.ledger import Ledger
from invoicer.models import Invoice, InvoiceDocument, LineItem, Party


def _invoice(confidence=0.95) -> Invoice:
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
        extraction_confidence=confidence,
    )


def _doc() -> InvoiceDocument:
    return InvoiceDocument(
        sender="a@b.pl", received_at=datetime(2026, 6, 1), filename="x.pdf", content=b"%PDF"
    )


def test_extract_node_sets_invoice_and_attempts():
    node = make_extract_node(StubExtractor(_invoice()))
    update = node({"document": _doc()})
    assert update["invoice"].number == "FV/1"
    assert update["extract_attempts"] == 1
    assert "errors" not in update  # wysoka pewnosc -> brak flagi


def test_extract_node_flags_low_confidence():
    node = make_extract_node(StubExtractor(_invoice(confidence=0.3)))
    update = node({"document": _doc()})
    assert update["errors"] and "pewnosc" in update["errors"][0].lower()


def test_validate_node_runs_validation_with_ledger(tmp_path):
    node = make_validate_node(Ledger(tmp_path / "l.jsonl"))
    update = node({"invoice": _invoice()})
    assert update["validation"].ok is True
    assert {c.name for c in update["validation"].checks} == {"nip", "sums", "lines", "duplicate"}
```

- [ ] **Step 2: Run to verify it fails** — `uv run pytest tests/unit/test_nodes.py -v` → FAIL (`ModuleNotFoundError: No module named 'invoicer.graph'`).

- [ ] **Step 3: Implement**

Create empty `src/invoicer/graph/__init__.py`.

Create `src/invoicer/graph/nodes.py`:
```python
from __future__ import annotations

from invoicer.ledger import Ledger
from invoicer.ports import InvoiceExtractor
from invoicer.state import InvoiceState
from invoicer.validation import validate_invoice

LOW_CONFIDENCE = 0.6


def make_extract_node(extractor: InvoiceExtractor):
    """Wezel `extract`: surowy dokument -> Invoice (przez wstrzyknięty ekstraktor)."""

    def extract(state: InvoiceState) -> dict:
        attempts = state.get("extract_attempts", 0) + 1
        invoice = extractor.extract(state["document"])
        update: dict = {"invoice": invoice, "extract_attempts": attempts}
        conf = invoice.extraction_confidence
        if conf is not None and conf < LOW_CONFIDENCE:
            update["errors"] = [f"Niska pewnosc ekstrakcji: {conf:.2f}"]
        return update

    return extract


def make_validate_node(ledger: Ledger):
    """Wezel `validate`: deterministyczna walidacja + wykrywanie duplikatow (ledger)."""

    def validate(state: InvoiceState) -> dict:
        return {"validation": validate_invoice(state["invoice"], ledger=ledger)}

    return validate
```

- [ ] **Step 4: Verify pass** — `uv run pytest tests/unit/test_nodes.py -v` → PASS (3). `uv run pytest -q` → green. `uv run ruff check . && uv run ruff format --check .` → clean.

- [ ] **Step 5: Commit**
```bash
git add src/invoicer/graph/__init__.py src/invoicer/graph/nodes.py tests/unit/test_nodes.py
git commit -m "feat: extract + validate graph nodes"
```

---

## Task 4: Węzeł classify (deterministyczny)

**Files:**
- Modify: `src/invoicer/graph/nodes.py`
- Test: `tests/unit/test_nodes.py`

- [ ] **Step 1: Add failing tests** — in `tests/unit/test_nodes.py`, MERGE the new imports into the TOP import block (ruff isort — do NOT scatter imports mid-file): add `classify_node` to the existing `from invoicer.graph.nodes import ...` line, and add `CountryBucket, TaxTreatment` to the existing `from invoicer.models import ...` line. Then append the new helper + tests below the existing ones:
```python
def _foreign_invoice() -> Invoice:
    inv = _invoice()
    inv.seller = Party(name="Foreign Ltd", country="GB", vat_id="GB123")
    inv.seller.nip = None
    inv.total_vat = Decimal("0.00")
    inv.total_gross = Decimal("1000.00")
    inv.currency = "GBP"
    inv.lines[0].vat = Decimal("0.00")
    inv.lines[0].vat_rate = Decimal("0.00")
    inv.lines[0].gross = Decimal("1000.00")
    return inv


def test_classify_domestic_pl():
    update = classify_node({"invoice": _invoice()})
    c = update["classification"]
    assert c.country_bucket == CountryBucket.PL
    assert c.treatment == TaxTreatment.KRAJOWA
    assert c.human_must_confirm == []


def test_classify_non_eu_uk_no_vat():
    update = classify_node({"invoice": _foreign_invoice()})
    c = update["classification"]
    assert c.country_bucket == CountryBucket.POZA_UE
    assert c.treatment == TaxTreatment.IMPORT_USLUG
    assert c.human_must_confirm  # czlowiek musi potwierdzic
    assert "GBP" in c.currency_note
```

- [ ] **Step 2: Run to verify it fails** — `uv run pytest tests/unit/test_nodes.py -k classify -v` → FAIL (`ImportError: cannot import name 'classify_node'`).

- [ ] **Step 3: Implement** — append to `src/invoicer/graph/nodes.py`.

Add ONE import to the top first-party group (alphabetically before `invoicer.ledger`) — `nodes.py` does NOT need `Decimal`, so do not add it:
```python
from invoicer.models import Classification, CountryBucket, TaxTreatment
```

Append at the END of the file:
```python
EU_COUNTRIES = frozenset(
    {
        "AT", "BE", "BG", "HR", "CY", "CZ", "DK", "EE", "FI", "FR", "DE", "GR",
        "HU", "IE", "IT", "LV", "LT", "LU", "MT", "NL", "PL", "PT", "RO", "SK",
        "SI", "ES", "SE",
    }
)


def classify_node(state: InvoiceState) -> dict:
    """Wezel `classify`: deterministyczne traktowanie podatkowe wg kraju sprzedawcy.

    PL -> krajowa. Zagranica -> domyslnie import uslug (odwrotne obciazenie),
    z lista rzeczy do potwierdzenia przez czlowieka. Bogate rozumowanie LLM
    (reason_exception) dochodzi w Planie 04.
    """
    invoice = state["invoice"]
    country = invoice.seller.country.upper()
    if country == "PL":
        classification = Classification(
            treatment=TaxTreatment.KRAJOWA,
            country_bucket=CountryBucket.PL,
            rationale_pl="Sprzedawca z PL — faktura krajowa.",
        )
    else:
        bucket = CountryBucket.UE if country in EU_COUNTRIES else CountryBucket.POZA_UE
        currency_note = (
            "" if invoice.currency == "PLN" else f"Waluta {invoice.currency} — przelicz po kursie NBP."
        )
        classification = Classification(
            treatment=TaxTreatment.IMPORT_USLUG,
            country_bucket=bucket,
            confidence=0.6,
            rationale_pl="Sprzedawca zagraniczny / brak VAT — domyslnie import uslug (odwrotne obciazenie).",
            human_must_confirm=[
                "usluga czy towar?",
                "stawka do samonaliczenia (zwykle 23%)",
                "kurs waluty (NBP z dnia poprzedzajacego)",
            ],
            currency_note=currency_note,
        )
    return {"classification": classification}
```

- [ ] **Step 4: Verify pass** — `uv run pytest tests/unit/test_nodes.py -v` → PASS (5). `uv run pytest -q` → green. `uv run ruff check . && uv run ruff format --check .` → clean (fix any line-length on the `currency_note` line by wrapping if ruff complains).

- [ ] **Step 5: Commit**
```bash
git add src/invoicer/graph/nodes.py tests/unit/test_nodes.py
git commit -m "feat: deterministic classify node (PL vs EU vs non-EU)"
```

---

## Task 5: Węzły human_review, routing i book

**Files:**
- Modify: `src/invoicer/graph/nodes.py`
- Test: `tests/unit/test_nodes.py`

- [ ] **Step 1: Add failing tests** — in `tests/unit/test_nodes.py`, MERGE the new imports into the TOP import block (ruff isort): add `make_book_node, route_after_review` to the existing `from invoicer.graph.nodes import ...` line; add `Classification` to the existing `from invoicer.models import ...` line (so it has `Classification, CountryBucket, Invoice, InvoiceDocument, LineItem, Party, TaxTreatment`); add a new line `from invoicer.adapters.mock_subiekt import MockSubiektSink`. Then append the new tests below the existing ones:
```python
def test_route_after_review_approve_goes_to_book():
    assert route_after_review({"human_decision": "approve"}) == "book"


def test_route_after_review_reject_goes_to_end():
    assert route_after_review({"human_decision": "reject"}) == "end"
    assert route_after_review({}) == "end"


def test_book_node_posts_and_records_ledger(tmp_path):
    ledger = Ledger(tmp_path / "l.jsonl")
    node = make_book_node(MockSubiektSink(), ledger, clock=lambda: "2026-06-01T10:00:00")
    inv = _invoice()
    classification = Classification(treatment=TaxTreatment.KRAJOWA, country_bucket=CountryBucket.PL)
    update = node({"invoice": inv, "classification": classification})
    assert update["booking"].booking_id == "MOCK-FV/1"
    assert ledger.is_duplicate(inv.number, inv.seller.nip, inv.seller.name) is True
    entry = ledger.entries()[0]
    assert entry.booked_at == "2026-06-01T10:00:00"
    assert entry.booking_id == "MOCK-FV/1"
```

- [ ] **Step 2: Run to verify it fails** — `uv run pytest tests/unit/test_nodes.py -k "route or book" -v` → FAIL (`ImportError: cannot import name 'make_book_node'`).

- [ ] **Step 3: Implement** — append to `src/invoicer/graph/nodes.py`.

Add to the top import block (stdlib + first-party groups, isort order):
```python
from collections.abc import Callable
from datetime import datetime

from langgraph.types import interrupt

from invoicer.booking import invoice_to_booking_payload
from invoicer.ledger import Ledger, LedgerEntry
from invoicer.ports import AccountingSink, InvoiceExtractor
```
(Merge with existing imports: `Ledger` is already imported — extend that line to `from invoicer.ledger import Ledger, LedgerEntry`; `InvoiceExtractor` is already imported from `invoicer.ports` — extend to `from invoicer.ports import AccountingSink, InvoiceExtractor`. Add the `collections.abc.Callable`, `datetime`, `langgraph.types.interrupt`, and `invoicer.booking` imports.)

Append at the END of the file:
```python
def human_review(state: InvoiceState) -> dict:
    """Wezel `human_review`: zatrzymuje graf (interrupt) i czeka na decyzje czlowieka.

    Zwracana wartosc resume (Command(resume=...)) trafia do human_decision.
    """
    invoice = state["invoice"]
    validation = state["validation"]
    classification = state["classification"]
    payload = {
        "number": invoice.number,
        "seller": invoice.seller.name,
        "country": invoice.seller.country,
        "total_gross": str(invoice.total_gross),
        "currency": invoice.currency,
        "validation_ok": validation.ok,
        "flags": [c.name for c in validation.hard_errors] + list(state.get("errors", [])),
        "treatment": str(classification.treatment),
        "rationale": classification.rationale_pl,
        "must_confirm": classification.human_must_confirm,
    }
    decision = interrupt(payload)
    return {"human_decision": decision}


def route_after_review(state: InvoiceState) -> str:
    """Krawedz warunkowa po human_review: tylko 'approve' prowadzi do ksiegowania."""
    return "book" if state.get("human_decision") == "approve" else "end"


def make_book_node(sink: AccountingSink, ledger: Ledger, clock: Callable[[], str] | None = None):
    """Wezel `book`: mapuje na dekret, ksieguje (sink) i dopisuje do ledger (audyt + duplikaty)."""
    clock = clock or (lambda: datetime.now().isoformat(timespec="seconds"))

    def book(state: InvoiceState) -> dict:
        invoice = state["invoice"]
        classification = state["classification"]
        payload = invoice_to_booking_payload(invoice, treatment=str(classification.treatment))
        result = sink.post(payload)
        ledger.append(
            LedgerEntry(
                number=invoice.number,
                seller_nip=invoice.seller.nip,
                seller_name=invoice.seller.name,
                total_gross=str(invoice.total_gross),
                booking_id=result.booking_id,
                booked_at=clock(),
            )
        )
        return {"booking": result}

    return book
```

- [ ] **Step 4: Verify pass** — `uv run pytest tests/unit/test_nodes.py -v` → PASS (8). `uv run pytest -q` → green. `uv run ruff check . && uv run ruff format --check .` → clean.

- [ ] **Step 5: Commit**
```bash
git add src/invoicer/graph/nodes.py tests/unit/test_nodes.py
git commit -m "feat: human_review (interrupt), routing, and book nodes"
```

---

## Task 6: Montaż grafu + test end-to-end

**Files:**
- Create: `src/invoicer/graph/build.py`
- Test: `tests/unit/test_graph.py`

- [ ] **Step 1: Write the failing test** — `tests/unit/test_graph.py`:
```python
from datetime import date, datetime
from decimal import Decimal

from langgraph.types import Command

from invoicer.adapters.mock_subiekt import MockSubiektSink
from invoicer.adapters.stub_extractor import StubExtractor
from invoicer.graph.build import build_invoice_graph
from invoicer.ledger import Ledger
from invoicer.models import Invoice, InvoiceDocument, LineItem, Party


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


def test_graph_pauses_at_human_review_then_books_on_approve(tmp_path):
    graph = _graph(tmp_path)
    config = {"configurable": {"thread_id": "t1"}}
    paused = graph.invoke({"document": _doc(), "errors": []}, config)
    # Graf zatrzymal sie na human_review -> jeszcze nie zaksiegowano.
    assert paused.get("booking") is None
    final = graph.invoke(Command(resume="approve"), config)
    assert final["booking"].booking_id == "MOCK-FV/1"


def test_graph_does_not_book_on_reject(tmp_path):
    ledger = Ledger(tmp_path / "l.jsonl")
    graph = build_invoice_graph(
        extractor=StubExtractor(_invoice()),
        ledger=ledger,
        sink=MockSubiektSink(),
        clock=lambda: "2026-06-01T10:00:00",
    )
    config = {"configurable": {"thread_id": "t2"}}
    graph.invoke({"document": _doc(), "errors": []}, config)
    final = graph.invoke(Command(resume="reject"), config)
    assert final.get("booking") is None
    assert ledger.entries() == []
```

- [ ] **Step 2: Run to verify it fails** — `uv run pytest tests/unit/test_graph.py -v` → FAIL (`ModuleNotFoundError: No module named 'invoicer.graph.build'`).

- [ ] **Step 3: Implement `src/invoicer/graph/build.py`**
```python
from __future__ import annotations

from collections.abc import Callable

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph

from invoicer.graph.nodes import (
    classify_node,
    human_review,
    make_book_node,
    make_extract_node,
    make_validate_node,
    route_after_review,
)
from invoicer.ledger import Ledger
from invoicer.ports import AccountingSink, InvoiceExtractor
from invoicer.state import InvoiceState


def build_invoice_graph(
    *,
    extractor: InvoiceExtractor,
    ledger: Ledger,
    sink: AccountingSink,
    clock: Callable[[], str] | None = None,
    checkpointer=None,
):
    """Montuje graf: extract -> validate -> classify -> human_review -> (book | end).

    Wymaga checkpointera (HITL/interrupt); domyslnie InMemorySaver.
    """
    builder = StateGraph(InvoiceState)
    builder.add_node("extract", make_extract_node(extractor))
    builder.add_node("validate", make_validate_node(ledger))
    builder.add_node("classify", classify_node)
    builder.add_node("human_review", human_review)
    builder.add_node("book", make_book_node(sink, ledger, clock=clock))

    builder.add_edge(START, "extract")
    builder.add_edge("extract", "validate")
    builder.add_edge("validate", "classify")
    builder.add_edge("classify", "human_review")
    builder.add_conditional_edges(
        "human_review", route_after_review, {"book": "book", "end": END}
    )
    builder.add_edge("book", END)

    return builder.compile(checkpointer=checkpointer or InMemorySaver())
```

> **NOTE (wersja LangGraph):** powyższy przepływ używa udokumentowanego wzorca
> `interrupt()` + `graph.invoke(Command(resume=...), config)`. Jeśli zainstalowana
> wersja LangGraph zasygnalizuje pauzę inaczej (np. trzeba sprawdzić
> `graph.get_state(config).next`), test `test_graph_*` to wychwyci na czerwono —
> dostosuj sposób wznawiania, NIE zmieniając kontraktu węzłów. Test asercją na
> `paused.get("booking") is None` + wznowienie jest odporny na dokładny kształt
> sygnału interrupt.

- [ ] **Step 4: Verify pass** — `uv run pytest tests/unit/test_graph.py -v` → PASS (2). `uv run pytest -q` → green. `uv run ruff check . && uv run ruff format --check .` → clean.

- [ ] **Step 5: Commit**
```bash
git add src/invoicer/graph/build.py tests/unit/test_graph.py
git commit -m "feat: build_invoice_graph (StateGraph + interrupt HITL + checkpointer)"
```

---

## Task 7: Driver CLI (process_document)

**Files:**
- Create: `src/invoicer/cli.py`
- Test: `tests/unit/test_cli.py`

- [ ] **Step 1: Write the failing test** — `tests/unit/test_cli.py`:
```python
from datetime import date, datetime
from decimal import Decimal

from invoicer.adapters.mock_subiekt import MockSubiektSink
from invoicer.adapters.stub_extractor import StubExtractor
from invoicer.cli import process_document
from invoicer.graph.build import build_invoice_graph
from invoicer.ledger import Ledger
from invoicer.models import Invoice, InvoiceDocument, LineItem, Party


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


def test_process_document_approve_books(tmp_path):
    seen = {}

    def decide(payload):
        seen.update(payload)
        return "approve"

    final = process_document(_graph(tmp_path), _doc(), thread_id="c1", decide=decide)
    assert seen["number"] == "FV/1"  # driver przekazal podsumowanie do decyzji
    assert final["booking"].booking_id == "MOCK-FV/1"


def test_process_document_reject_does_not_book(tmp_path):
    final = process_document(_graph(tmp_path), _doc(), thread_id="c2", decide=lambda p: "reject")
    assert final.get("booking") is None
```

- [ ] **Step 2: Run to verify it fails** — `uv run pytest tests/unit/test_cli.py -v` → FAIL (`ModuleNotFoundError: No module named 'invoicer.cli'`).

- [ ] **Step 3: Implement `src/invoicer/cli.py`**
```python
from __future__ import annotations

from collections.abc import Callable

from langgraph.types import Command

from invoicer.models import InvoiceDocument
from invoicer.state import InvoiceState


def process_document(
    graph,
    document: InvoiceDocument,
    *,
    thread_id: str,
    decide: Callable[[dict], str],
) -> InvoiceState:
    """Przeprowadza jeden dokument przez graf z bramka czlowieka.

    `decide(payload) -> "approve" | "reject"` dostaje podsumowanie z human_review.
    Domyslna implementacja CLI (Rich) wstrzykiwana jest przez wolajacego.
    """
    config = {"configurable": {"thread_id": thread_id}}
    result = graph.invoke({"document": document, "errors": []}, config)
    interrupts = result.get("__interrupt__")
    if interrupts:
        payload = interrupts[0].value
        result = graph.invoke(Command(resume=decide(payload)), config)
    return result
```

> **NOTE (kształt interrupt):** `result["__interrupt__"]` to lista obiektów
> `Interrupt` z polem `.value` (payload przekazany do `interrupt(...)`). Jeśli
> zainstalowana wersja LangGraph eksponuje to inaczej, test `test_cli` pokaże to
> na czerwono — dostosuj wyłącznie sposób wyłuskania payloadu/wznowienia.

- [ ] **Step 4: Verify pass** — `uv run pytest tests/unit/test_cli.py -v` → PASS (2). `uv run pytest -q` → green. `uv run ruff check . && uv run ruff format --check .` → clean.

- [ ] **Step 5: Commit**
```bash
git add src/invoicer/cli.py tests/unit/test_cli.py
git commit -m "feat: process_document CLI driver (interrupt -> decide -> resume)"
```

---

## Task 8: Lint + pełny suite (zielona baza)

**Files:** (kontrola jakości całości)

- [ ] **Step 1: Ruff lint** — `cd /Users/mski/Developer/Invoicer && uv run ruff check .` → `All checks passed!` (lub `--fix`, potem ponów).
- [ ] **Step 2: Ruff format** — `uv run ruff format --check .` → sformatowane (lub `uv run ruff format .`, potem commit).
- [ ] **Step 3: Pełny suite** — `uv run pytest -q` → wszystko zielone. Oczekiwany przyrost: Plan 02 = 46, Plan 03 dodaje 3+3+5+8(skum.)... policz rzeczywiście (test_classification 3, test_state 3, test_nodes 8, test_graph 2, test_cli 2 = +18 → ~64). Zweryfikuj i zaraportuj realną liczbę.
- [ ] **Step 4: Commit (jeśli ruff coś zmienił)**
```bash
cd /Users/mski/Developer/Invoicer && git add -A && git commit -m "chore: ruff clean, green suite (Plan 03 graph+HITL done)" || echo "nic do commita"
```

---

## Self-Review (wykonane przy pisaniu planu)

**Spec coverage (Plan 03 = Kamień 3; sekcja 4, część 5/7):**
- Stan grafu `InvoiceState` (sek. 4) → Task 2 ✓
- Węzły extract/validate/classify/human_review/book (sek. 4) → Tasks 3–5 ✓
- Krawędzie + montaż `StateGraph` + checkpointer (sek. 4) → Task 6 ✓
- Bramka HITL `interrupt()` (sek. 4, 7) → Tasks 5–6 ✓
- `Classification` (sek. 5) → Task 1 ✓
- Idempotencja: book dopisuje do ledger; powtórne księgowanie wykrywa duplikat z Planu 02 → Tasks 5–6 ✓
- Port `InvoiceExtractor` + `StubExtractor` (testowalność) → Task 2 ✓
- **Świadomie poza Planem 03 (do Planu 04):** realny `ClaudeVisionExtractor` (langchain-anthropic, vision, structured output) i `reason_exception` (sędzia-LLM, bogata klasyfikacja zagraniczna). Klasyfikacja tu jest deterministyczna. Pełne bramki sek. 8 (sędzia-LLM, limity budżetu tokenów) — częściowo (recursion_limit + flaga niskiej pewności + licznik prób); reszta przy realnym LLM.

**Placeholder scan:** brak TBD/TODO; każdy krok ma pełny kod, komendy, oczekiwany wynik. Dwie świadome NOTE-ki dot. wersji LangGraph (kształt interrupt) — z jasną instrukcją, że uruchamiany test jest siatką bezpieczeństwa.

**Type consistency:** `InvoiceState` (TypedDict, total=False); węzły zwracają częściowe `dict`; fabryki `make_extract_node(extractor)`, `make_validate_node(ledger)`, `make_book_node(sink, ledger, clock=None)`; `classify_node`/`human_review`/`route_after_review` bez fabryki; `build_invoice_graph(*, extractor, ledger, sink, clock=None, checkpointer=None)`; `process_document(graph, document, *, thread_id, decide)`. `Classification(treatment, country_bucket, confidence=1.0, rationale_pl="", human_must_confirm=[], currency_note="")`. `route_after_review` zwraca `"book"`/`"end"` zgodnie z mapą w `add_conditional_edges`. `make_book_node` ustawia `booked_at=clock()` (jedno źródło zegara — domyka uwagę z finałowego review Planu 02).

**Uwaga wykonawcza:** węzły dotykające I/O budowane są fabrykami domkniętymi na zależnościach — `build_invoice_graph` wstrzykuje je, więc graf jest deterministycznie testowalny (StubExtractor, fake ledger w tmp_path, mock sink, stały zegar). To samo wstrzyknięcie pozwoli w Planie 04 podmienić StubExtractor na ClaudeVisionExtractor bez zmiany grafu.
