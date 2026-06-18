# Invoicer — Plan 02: Ports & Ledger Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Dodać warstwę portów/adapterów (wzorzec porty-adaptery) oraz append-only ledger z wykrywaniem duplikatów, tak by rdzeń był odpięty od konkretnego I/O (Gmail, Subiekt) i odporny na podwójne księgowanie.

**Architecture:** Definiujemy interfejsy (`EmailSource`, `AccountingSink`) jako `typing.Protocol`. Implementacje mock: `FixtureSource` (czyta PDF-y z lokalnego katalogu) i `MockSubiektSink` (loguje gotowy dekret). `Ledger` to plik JSONL (append-only) z kluczem duplikatu `(numer, NIP|nazwa sprzedawcy)`. Wykrywanie duplikatu wpinamy w `validate_invoice` przez wstrzykiwany `ledger` (pozostaje czyste i testowalne). Realne adaptery (Gmail, Sfera) to Plany 06+.

**Tech Stack:** Python 3.12, uv, Pydantic v2, `typing.Protocol` (`runtime_checkable`), pytest (`tmp_path`), ruff. Bez nowych zależności runtime.

**Spec:** `docs/superpowers/specs/2026-06-18-invoicer-design.md` — realizuje Kamień milowy 2 oraz sekcje 3 (porty/adaptery), 5 (`BookingPayload`, `is_duplicate`) i 6 (duplikaty).

**Stan wyjściowy:** Plan 01 scalony do `master`. Istnieją: `src/invoicer/models.py` (`Party`, `LineItem`, `Invoice`, `Check`, `CheckStatus`, `ValidationResult`), `src/invoicer/validation.py` (`nip_checksum_valid`, `totals_consistent`, `validate_invoice(invoice)`), 20 zielonych testów, ruff E/F/I/UP/B + line-length 100. Praca na nowej gałęzi `feat/plan-02-ports-ledger`. Komendy przez `uv run`. Importy trzymamy na górze plików (isort "I" włączony).

---

## File Structure

| Plik | Odpowiedzialność |
|------|------------------|
| `src/invoicer/models.py` (MOD) | + `InvoiceDocument` (surowy załącznik); + `is_duplicate` na `ValidationResult`. |
| `src/invoicer/booking.py` (NEW) | `BookingPayload`, `BookingResult`, `invoice_to_booking_payload` — dekret dla ujścia księgowego. |
| `src/invoicer/ports.py` (NEW) | Protokoły `EmailSource`, `AccountingSink`. |
| `src/invoicer/ledger.py` (NEW) | `LedgerEntry`, `Ledger` (JSONL append-only, `is_duplicate`). |
| `src/invoicer/adapters/__init__.py` (NEW) | Marker pakietu adapterów. |
| `src/invoicer/adapters/fixture_source.py` (NEW) | `FixtureSource` — `EmailSource` z lokalnego katalogu. |
| `src/invoicer/adapters/mock_subiekt.py` (NEW) | `MockSubiektSink` — `AccountingSink` logujący dekret. |
| `src/invoicer/validation.py` (MOD) | `validate_invoice(invoice, ledger=None)` — check "duplicate". |
| `tests/unit/test_documents.py` (NEW) | `InvoiceDocument` + domyślne `is_duplicate`. |
| `tests/unit/test_booking.py` (NEW) | mapper + modele dekretu. |
| `tests/unit/test_ports.py` (NEW) | konformność Protokołów. |
| `tests/unit/test_ledger.py` (NEW) | append/entries/is_duplicate. |
| `tests/unit/test_fixture_source.py` (NEW) | wczytywanie i filtr nadawcy. |
| `tests/unit/test_mock_subiekt.py` (NEW) | post() → BookingResult. |
| `tests/unit/test_validation_duplicates.py` (NEW) | duplikat w `validate_invoice`. |

**Klucz duplikatu (spec §6):** `(numer faktury, NIP sprzedawcy)`, a gdy sprzedawca nie ma NIP (zagraniczny) → `(numer, nazwa sprzedawcy)`. Duplikat = twardy błąd (FAIL → `ok=False`), bo blokuje podwójne księgowanie.

---

## Task 0: Gałąź robocza

- [ ] **Step 1: Utwórz i przełącz gałąź**

Run:
```bash
cd /Users/mski/Developer/Invoicer && git checkout master && git checkout -b feat/plan-02-ports-ledger
```
Expected: `Switched to a new branch 'feat/plan-02-ports-ledger'`.

---

## Task 1: `InvoiceDocument` + `ValidationResult.is_duplicate`

**Files:**
- Modify: `src/invoicer/models.py`
- Test: `tests/unit/test_documents.py`

- [ ] **Step 1: Write the failing test** — `tests/unit/test_documents.py`:
```python
from datetime import datetime

from invoicer.models import InvoiceDocument, ValidationResult


def test_invoice_document_holds_raw_attachment():
    doc = InvoiceDocument(
        sender="ksiegowa@klient.pl",
        subject="Faktura 06/2026",
        received_at=datetime(2026, 6, 1, 10, 0, 0),
        filename="faktura.pdf",
        content=b"%PDF-1.4 dane",
    )
    assert doc.sender == "ksiegowa@klient.pl"
    assert doc.filename == "faktura.pdf"
    assert doc.content.startswith(b"%PDF")


def test_invoice_document_subject_optional():
    doc = InvoiceDocument(
        sender="a@b.pl",
        received_at=datetime(2026, 1, 1, 0, 0, 0),
        filename="x.pdf",
        content=b"x",
    )
    assert doc.subject == ""


def test_validation_result_is_duplicate_defaults_false():
    vr = ValidationResult(checks=[])
    assert vr.is_duplicate is False
```

- [ ] **Step 2: Run test to verify it fails** — `uv run pytest tests/unit/test_documents.py -v` → FAIL (`ImportError: cannot import name 'InvoiceDocument'`).

- [ ] **Step 3: Modify `src/invoicer/models.py`**

Change the datetime import (line 3) from:
```python
from datetime import date
```
to:
```python
from datetime import date, datetime
```

Add this class immediately AFTER the `Invoice` class (after its last field, before `class CheckStatus`):
```python
class InvoiceDocument(BaseModel):
    """Surowy dokument wejsciowy (zalacznik e-mail) zanim nastapi ekstrakcja."""

    sender: str
    received_at: datetime
    filename: str
    content: bytes
    subject: str = ""
```

Add `is_duplicate` as a field on `ValidationResult` (insert it as the first line of the class body, before the `checks` property block — i.e., right after `class ValidationResult(BaseModel):`):
```python
class ValidationResult(BaseModel):
    checks: list[Check]
    is_duplicate: bool = False

    @property
    def hard_errors(self) -> list[Check]:
        return [c for c in self.checks if c.status == CheckStatus.FAIL]

    @property
    def soft_flags(self) -> list[Check]:
        return [c for c in self.checks if c.status == CheckStatus.WARN]

    @property
    def ok(self) -> bool:
        return not self.hard_errors
```

- [ ] **Step 4: Run tests to verify pass** — `uv run pytest tests/unit/test_documents.py -v` → PASS (3). Then `uv run pytest -q` → all prior still green (existing `test_models.py`/`test_validation.py` unaffected: `is_duplicate` has a default).

- [ ] **Step 5: Lint + commit**
```bash
cd /Users/mski/Developer/Invoicer && uv run ruff check . && uv run ruff format --check .
git add src/invoicer/models.py tests/unit/test_documents.py
git commit -m "feat: InvoiceDocument model + ValidationResult.is_duplicate"
```
(If `ruff format --check` reports a file, run `uv run ruff format .`, re-add, and include in the commit.)

---

## Task 2: `BookingPayload` / `BookingResult` + mapper

**Files:**
- Create: `src/invoicer/booking.py`
- Test: `tests/unit/test_booking.py`

- [ ] **Step 1: Write the failing test** — `tests/unit/test_booking.py`:
```python
from datetime import date
from decimal import Decimal

from invoicer.booking import BookingPayload, BookingResult, invoice_to_booking_payload
from invoicer.models import Invoice, LineItem, Party


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
        number="FV/2026/06/01",
        issue_date=date(2026, 6, 1),
        currency="PLN",
        lines=[line],
        total_net=Decimal("1000.00"),
        total_vat=Decimal("230.00"),
        total_gross=Decimal("1230.00"),
    )


def test_mapper_copies_core_fields():
    payload = invoice_to_booking_payload(_invoice())
    assert isinstance(payload, BookingPayload)
    assert payload.number == "FV/2026/06/01"
    assert payload.seller.name == "ACME"
    assert payload.total_gross == Decimal("1230.00")
    assert payload.currency == "PLN"
    assert payload.treatment is None


def test_mapper_carries_treatment_when_given():
    payload = invoice_to_booking_payload(_invoice(), treatment="import_uslug")
    assert payload.treatment == "import_uslug"


def test_booking_result_defaults_status_posted():
    res = BookingResult(booking_id="MOCK-1", sink="mock-subiekt")
    assert res.status == "posted"
```

- [ ] **Step 2: Run test to verify it fails** — `uv run pytest tests/unit/test_booking.py -v` → FAIL (`ModuleNotFoundError: No module named 'invoicer.booking'`).

- [ ] **Step 3: Implement `src/invoicer/booking.py`**:
```python
from __future__ import annotations

from decimal import Decimal

from pydantic import BaseModel

from invoicer.models import Invoice, LineItem, Party


class BookingPayload(BaseModel):
    """Znormalizowany dekret przekazywany do AccountingSink (mock Subiekt / realny Sfera)."""

    seller: Party
    buyer: Party
    number: str
    currency: str
    lines: list[LineItem]
    total_net: Decimal
    total_vat: Decimal
    total_gross: Decimal
    treatment: str | None = None  # traktowanie podatkowe — uzupelnia klasyfikacja (Plan 04)


class BookingResult(BaseModel):
    booking_id: str
    sink: str
    status: str = "posted"


def invoice_to_booking_payload(invoice: Invoice, treatment: str | None = None) -> BookingPayload:
    """Mapuje zwalidowana fakture na niezalezny snapshot-dekret dla programu ksiegowego.

    Zagniezdzone modele (seller/buyer/lines) sa kopiowane (deep), wiec pozniejsze
    zmiany faktury nie wplywaja na juz utworzony dekret (ani odwrotnie).
    """
    return BookingPayload(
        seller=invoice.seller.model_copy(deep=True),
        buyer=invoice.buyer.model_copy(deep=True),
        number=invoice.number,
        currency=invoice.currency,
        lines=[line.model_copy(deep=True) for line in invoice.lines],
        total_net=invoice.total_net,
        total_vat=invoice.total_vat,
        total_gross=invoice.total_gross,
        treatment=treatment,
    )
```

- [ ] **Step 4: Run test to verify pass** — `uv run pytest tests/unit/test_booking.py -v` → PASS (3).

- [ ] **Step 5: Lint + commit**
```bash
cd /Users/mski/Developer/Invoicer && uv run ruff check . && uv run ruff format --check .
git add src/invoicer/booking.py tests/unit/test_booking.py
git commit -m "feat: BookingPayload/BookingResult + invoice_to_booking_payload"
```

---

## Task 3: Porty `EmailSource` / `AccountingSink`

**Files:**
- Create: `src/invoicer/ports.py`
- Test: `tests/unit/test_ports.py`

- [ ] **Step 1: Write the failing test** — `tests/unit/test_ports.py`:
```python
from invoicer.ports import AccountingSink, EmailSource


def test_email_source_accepts_conforming_impl():
    class _Fake:
        def fetch(self, sender: str):
            return []

    assert isinstance(_Fake(), EmailSource)


def test_email_source_rejects_nonconforming_impl():
    class _NoFetch:
        pass

    assert not isinstance(_NoFetch(), EmailSource)


def test_accounting_sink_accepts_conforming_impl():
    class _Fake:
        def post(self, payload):
            return None

    assert isinstance(_Fake(), AccountingSink)


def test_accounting_sink_rejects_nonconforming_impl():
    class _NoPost:
        pass

    assert not isinstance(_NoPost(), AccountingSink)
```

- [ ] **Step 2: Run test to verify it fails** — `uv run pytest tests/unit/test_ports.py -v` → FAIL (`ModuleNotFoundError: No module named 'invoicer.ports'`).

- [ ] **Step 3: Implement `src/invoicer/ports.py`**:
```python
from __future__ import annotations

from typing import Protocol, runtime_checkable

from invoicer.booking import BookingPayload, BookingResult
from invoicer.models import InvoiceDocument


@runtime_checkable
class EmailSource(Protocol):
    """Zrodlo dokumentow: pobiera zalaczniki-faktury od konkretnego nadawcy."""

    def fetch(self, sender: str) -> list[InvoiceDocument]: ...


@runtime_checkable
class AccountingSink(Protocol):
    """Ujscie ksiegowe: przyjmuje gotowy dekret i zwraca wynik zaksiegowania."""

    def post(self, payload: BookingPayload) -> BookingResult: ...
```

- [ ] **Step 4: Run test to verify pass** — `uv run pytest tests/unit/test_ports.py -v` → PASS (4).

- [ ] **Step 5: Lint + commit**
```bash
cd /Users/mski/Developer/Invoicer && uv run ruff check . && uv run ruff format --check .
git add src/invoicer/ports.py tests/unit/test_ports.py
git commit -m "feat: EmailSource/AccountingSink port protocols"
```

---

## Task 4: `Ledger` (append-only JSONL + duplikaty)

**Files:**
- Create: `src/invoicer/ledger.py`
- Test: `tests/unit/test_ledger.py`

- [ ] **Step 1: Write the failing test** — `tests/unit/test_ledger.py`:
```python
from invoicer.ledger import Ledger, LedgerEntry


def _entry(number: str, nip: str | None, name: str) -> LedgerEntry:
    return LedgerEntry(
        number=number,
        seller_nip=nip,
        seller_name=name,
        total_gross="1230.00",
        booking_id="MOCK-1",
        booked_at="2026-06-01T10:00:00",
    )


def test_append_and_read_roundtrip(tmp_path):
    ledger = Ledger(tmp_path / "ledger.jsonl")
    ledger.append(_entry("FV/1", "5260001246", "ACME"))
    ledger.append(_entry("FV/2", "5260001246", "ACME"))
    entries = ledger.entries()
    assert [e.number for e in entries] == ["FV/1", "FV/2"]


def test_entries_empty_when_file_absent(tmp_path):
    ledger = Ledger(tmp_path / "missing.jsonl")
    assert ledger.entries() == []


def test_is_duplicate_matches_number_and_nip(tmp_path):
    ledger = Ledger(tmp_path / "ledger.jsonl")
    ledger.append(_entry("FV/1", "5260001246", "ACME"))
    assert ledger.is_duplicate("FV/1", "5260001246", "ACME") is True
    assert ledger.is_duplicate("FV/9", "5260001246", "ACME") is False


def test_is_duplicate_falls_back_to_name_when_no_nip(tmp_path):
    ledger = Ledger(tmp_path / "ledger.jsonl")
    ledger.append(_entry("INV/7", None, "Foreign Ltd"))
    assert ledger.is_duplicate("INV/7", None, "Foreign Ltd") is True
    assert ledger.is_duplicate("INV/7", None, "Other Ltd") is False
```

- [ ] **Step 2: Run test to verify it fails** — `uv run pytest tests/unit/test_ledger.py -v` → FAIL (`ModuleNotFoundError: No module named 'invoicer.ledger'`).

- [ ] **Step 3: Implement `src/invoicer/ledger.py`**:
```python
from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel


class LedgerEntry(BaseModel):
    number: str
    seller_name: str
    total_gross: str  # Decimal jako string — stabilny zapis JSON
    booking_id: str
    booked_at: str  # ISO-8601, ustawiane przez wolajacego (determinizm)
    seller_nip: str | None = None


def _dedup_key(number: str, seller_nip: str | None, seller_name: str) -> tuple[str, str]:
    return (number, seller_nip or seller_name)


class Ledger:
    """Append-only rejestr zaksiegowanych faktur (JSONL) z wykrywaniem duplikatow.

    Klucz duplikatu: (numer, NIP sprzedawcy) albo (numer, nazwa) gdy brak NIP.
    """

    def __init__(self, path: Path) -> None:
        self.path = path

    def append(self, entry: LedgerEntry) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as f:
            f.write(entry.model_dump_json() + "\n")

    def entries(self) -> list[LedgerEntry]:
        if not self.path.exists():
            return []
        return [
            LedgerEntry.model_validate_json(line)
            for line in self.path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]

    def is_duplicate(self, number: str, seller_nip: str | None, seller_name: str) -> bool:
        key = _dedup_key(number, seller_nip, seller_name)
        return any(
            _dedup_key(e.number, e.seller_nip, e.seller_name) == key for e in self.entries()
        )
```

- [ ] **Step 4: Run test to verify pass** — `uv run pytest tests/unit/test_ledger.py -v` → PASS (4).

- [ ] **Step 5: Lint + commit**
```bash
cd /Users/mski/Developer/Invoicer && uv run ruff check . && uv run ruff format --check .
git add src/invoicer/ledger.py tests/unit/test_ledger.py
git commit -m "feat: append-only Ledger (JSONL) with duplicate detection"
```

---

## Task 5: Adapter `FixtureSource`

**Files:**
- Create: `src/invoicer/adapters/__init__.py`
- Create: `src/invoicer/adapters/fixture_source.py`
- Test: `tests/unit/test_fixture_source.py`

- [ ] **Step 1: Write the failing test** — `tests/unit/test_fixture_source.py`:
```python
import json

from invoicer.adapters.fixture_source import FixtureSource
from invoicer.ports import EmailSource


def _write_fixture(directory, name, sender, content=b"%PDF-1.4 x"):
    (directory / f"{name}.pdf").write_bytes(content)
    (directory / f"{name}.json").write_text(
        json.dumps(
            {"sender": sender, "subject": "Faktura", "received_at": "2026-06-01T10:00:00"}
        ),
        encoding="utf-8",
    )


def test_fixture_source_satisfies_email_source_protocol(tmp_path):
    assert isinstance(FixtureSource(tmp_path), EmailSource)


def test_fetch_filters_by_sender(tmp_path):
    _write_fixture(tmp_path, "a", "ksiegowa@klient.pl")
    _write_fixture(tmp_path, "b", "ktos@inny.pl")
    docs = FixtureSource(tmp_path).fetch("ksiegowa@klient.pl")
    assert len(docs) == 1
    assert docs[0].filename == "a.pdf"
    assert docs[0].sender == "ksiegowa@klient.pl"
    assert docs[0].content.startswith(b"%PDF")


def test_fetch_returns_empty_for_unknown_sender(tmp_path):
    _write_fixture(tmp_path, "a", "ksiegowa@klient.pl")
    assert FixtureSource(tmp_path).fetch("nieznany@x.pl") == []
```

- [ ] **Step 2: Run test to verify it fails** — `uv run pytest tests/unit/test_fixture_source.py -v` → FAIL (`ModuleNotFoundError: No module named 'invoicer.adapters'`).

- [ ] **Step 3: Implement**

Create empty `src/invoicer/adapters/__init__.py` (no content needed).

Create `src/invoicer/adapters/fixture_source.py`:
```python
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from invoicer.models import InvoiceDocument


class FixtureSource:
    """EmailSource oparty o lokalny katalog fixture'ow (testy i demo offline).

    Sidecar `<name>.json` jest WYMAGANY dla kazdego `<name>.pdf`:
    {"sender": "...", "subject": "...", "received_at": "2026-06-01T10:00:00"}.
    sender/subject opcjonalne w sidecarze; received_at wymagane. Brak sidecara
    lub katalogu => glosny blad (fail-fast), nie cichy default.
    """

    def __init__(self, directory: Path) -> None:
        self.directory = directory

    def _load(self) -> list[InvoiceDocument]:
        if not self.directory.is_dir():
            raise NotADirectoryError(f"Katalog fixture'ow nie istnieje: {self.directory}")
        docs: list[InvoiceDocument] = []
        for pdf in sorted(self.directory.glob("*.pdf")):
            meta_path = pdf.with_suffix(".json")
            if not meta_path.exists():
                raise FileNotFoundError(
                    f"Brak sidecara metadanych '{meta_path.name}' dla fixture '{pdf.name}'"
                )
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            docs.append(
                InvoiceDocument(
                    sender=meta.get("sender", ""),
                    subject=meta.get("subject", ""),
                    received_at=datetime.fromisoformat(meta["received_at"]),
                    filename=pdf.name,
                    content=pdf.read_bytes(),
                )
            )
        return docs

    def fetch(self, sender: str) -> list[InvoiceDocument]:
        return [doc for doc in self._load() if doc.sender == sender]
```

> Uwaga (decyzja z review): sidecar i katalog są wymagane — zepsuty fixture ma
> być głośny. Dochodzą 2 testy: `test_fetch_raises_when_sidecar_missing`,
> `test_fetch_raises_when_directory_missing`.

- [ ] **Step 4: Run test to verify pass** — `uv run pytest tests/unit/test_fixture_source.py -v` → PASS (3).

- [ ] **Step 5: Lint + commit**
```bash
cd /Users/mski/Developer/Invoicer && uv run ruff check . && uv run ruff format --check .
git add src/invoicer/adapters/__init__.py src/invoicer/adapters/fixture_source.py tests/unit/test_fixture_source.py
git commit -m "feat: FixtureSource adapter (EmailSource from local PDFs)"
```

---

## Task 6: Adapter `MockSubiektSink`

**Files:**
- Create: `src/invoicer/adapters/mock_subiekt.py`
- Test: `tests/unit/test_mock_subiekt.py`

- [ ] **Step 1: Write the failing test** — `tests/unit/test_mock_subiekt.py`:
```python
import logging
from datetime import date
from decimal import Decimal

from invoicer.adapters.mock_subiekt import MockSubiektSink
from invoicer.booking import BookingResult, invoice_to_booking_payload
from invoicer.models import Invoice, LineItem, Party
from invoicer.ports import AccountingSink


def _payload():
    line = LineItem(
        description="Usluga",
        quantity=Decimal("1"),
        unit_net=Decimal("1000.00"),
        vat_rate=Decimal("0.23"),
        net=Decimal("1000.00"),
        vat=Decimal("230.00"),
        gross=Decimal("1230.00"),
    )
    invoice = Invoice(
        seller=Party(name="ACME", nip="5260001246", country="PL"),
        buyer=Party(name="Klient", country="PL"),
        number="FV/2026/06/01",
        issue_date=date(2026, 6, 1),
        currency="PLN",
        lines=[line],
        total_net=Decimal("1000.00"),
        total_vat=Decimal("230.00"),
        total_gross=Decimal("1230.00"),
    )
    return invoice_to_booking_payload(invoice)


def test_mock_subiekt_satisfies_accounting_sink_protocol():
    assert isinstance(MockSubiektSink(), AccountingSink)


def test_post_returns_deterministic_booking_result():
    res = MockSubiektSink().post(_payload())
    assert isinstance(res, BookingResult)
    assert res.booking_id == "MOCK-FV/2026/06/01"
    assert res.status == "posted"
    assert res.sink == "mock-subiekt"


def test_post_logs_the_decree(caplog):
    with caplog.at_level(logging.INFO, logger="invoicer.mock_subiekt"):
        MockSubiektSink().post(_payload())
    assert "FV/2026/06/01" in caplog.text
```

- [ ] **Step 2: Run test to verify it fails** — `uv run pytest tests/unit/test_mock_subiekt.py -v` → FAIL (`ModuleNotFoundError: No module named 'invoicer.adapters.mock_subiekt'`).

- [ ] **Step 3: Implement `src/invoicer/adapters/mock_subiekt.py`**:
```python
from __future__ import annotations

import logging

from invoicer.booking import BookingPayload, BookingResult

logger = logging.getLogger("invoicer.mock_subiekt")


class MockSubiektSink:
    """AccountingSink udajacy Subiekt: loguje dekret i zwraca deterministyczny wynik.

    Realny zapis do Subiekt GT wymaga Windows + Sfera (COM) — patrz spec, SubiektSferaSink.
    """

    sink_name = "mock-subiekt"

    def post(self, payload: BookingPayload) -> BookingResult:
        booking_id = f"MOCK-{payload.number}"
        logger.info(
            "Zaksiegowano (mock): numer=%s sprzedawca=%s brutto=%s waluta=%s traktowanie=%s",
            payload.number,
            payload.seller.name,
            payload.total_gross,
            payload.currency,
            payload.treatment,
        )
        return BookingResult(booking_id=booking_id, sink=self.sink_name)
```

- [ ] **Step 4: Run test to verify pass** — `uv run pytest tests/unit/test_mock_subiekt.py -v` → PASS (3).

- [ ] **Step 5: Lint + commit**
```bash
cd /Users/mski/Developer/Invoicer && uv run ruff check . && uv run ruff format --check .
git add src/invoicer/adapters/mock_subiekt.py tests/unit/test_mock_subiekt.py
git commit -m "feat: MockSubiektSink adapter (AccountingSink logs decree)"
```

---

## Task 7: Wykrywanie duplikatów w `validate_invoice`

**Files:**
- Modify: `src/invoicer/validation.py`
- Test: `tests/unit/test_validation_duplicates.py`

- [ ] **Step 1: Write the failing test** — `tests/unit/test_validation_duplicates.py`:
```python
from datetime import date
from decimal import Decimal

from invoicer.ledger import Ledger, LedgerEntry
from invoicer.models import CheckStatus, Invoice, LineItem, Party
from invoicer.validation import validate_invoice


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
        number="FV/2026/06/01",
        issue_date=date(2026, 6, 1),
        currency="PLN",
        lines=[line],
        total_net=Decimal("1000.00"),
        total_vat=Decimal("230.00"),
        total_gross=Decimal("1230.00"),
    )


def _ledger_with_invoice(tmp_path, invoice: Invoice) -> Ledger:
    ledger = Ledger(tmp_path / "ledger.jsonl")
    ledger.append(
        LedgerEntry(
            number=invoice.number,
            seller_nip=invoice.seller.nip,
            seller_name=invoice.seller.name,
            total_gross=str(invoice.total_gross),
            booking_id="MOCK-1",
            booked_at="2026-06-01T10:00:00",
        )
    )
    return ledger


def test_no_ledger_means_no_duplicate_check():
    vr = validate_invoice(_invoice())
    assert vr.is_duplicate is False
    assert {c.name for c in vr.checks} == {"nip", "sums", "lines"}


def test_ledger_without_match_passes_duplicate_check(tmp_path):
    ledger = Ledger(tmp_path / "empty.jsonl")
    vr = validate_invoice(_invoice(), ledger=ledger)
    assert vr.is_duplicate is False
    dup = next(c for c in vr.checks if c.name == "duplicate")
    assert dup.status == CheckStatus.PASS
    assert vr.ok is True


def test_duplicate_invoice_fails(tmp_path):
    inv = _invoice()
    ledger = _ledger_with_invoice(tmp_path, inv)
    vr = validate_invoice(inv, ledger=ledger)
    assert vr.is_duplicate is True
    dup = next(c for c in vr.checks if c.name == "duplicate")
    assert dup.status == CheckStatus.FAIL
    assert vr.ok is False
```

- [ ] **Step 2: Run test to verify it fails** — `uv run pytest tests/unit/test_validation_duplicates.py -v` → FAIL: the `test_ledger_*` cases error with `TypeError: validate_invoice() got an unexpected keyword argument 'ledger'`.

- [ ] **Step 3: Modify `src/invoicer/validation.py`**

Add the ledger import at the top (first-party group, alphabetical — `ledger` before `models`):
```python
from invoicer.ledger import Ledger
from invoicer.models import Check, CheckStatus, Invoice, ValidationResult
```

Replace the `validate_invoice` signature, docstring, and the final `return` so the whole function reads:
```python
def validate_invoice(invoice: Invoice, ledger: Ledger | None = None) -> ValidationResult:
    """Łączy kontrole deterministyczne w jeden ValidationResult.

    NIP wymagany tylko dla sprzedawcy z PL; zagraniczny → WARN (nie FAIL).
    Gdy podano `ledger`, dokladany jest check "duplicate" (FAIL = duplikat) i ustawiane
    jest is_duplicate. Bez `ledger` duplikaty nie sa sprawdzane (is_duplicate=False).
    """
    checks: list[Check] = []

    if invoice.seller.country == "PL":
        if nip_checksum_valid(invoice.seller.nip):
            checks.append(Check(name="nip", status=CheckStatus.PASS))
        else:
            checks.append(
                Check(
                    name="nip",
                    status=CheckStatus.FAIL,
                    detail="Niepoprawny NIP sprzedawcy (suma kontrolna)",
                )
            )
    else:
        checks.append(
            Check(
                name="nip",
                status=CheckStatus.WARN,
                detail="Sprzedawca zagraniczny — NIP PL nie dotyczy",
            )
        )

    if totals_consistent(invoice):
        checks.append(Check(name="sums", status=CheckStatus.PASS))
    else:
        checks.append(
            Check(
                name="sums",
                status=CheckStatus.FAIL,
                detail="Niespojne sumy (netto+VAT≠brutto lub Σ pozycji)",
            )
        )

    if invoice.lines:
        checks.append(Check(name="lines", status=CheckStatus.PASS))
    else:
        checks.append(Check(name="lines", status=CheckStatus.FAIL, detail="Brak pozycji"))

    is_duplicate = False
    if ledger is not None:
        is_duplicate = ledger.is_duplicate(
            invoice.number, invoice.seller.nip, invoice.seller.name
        )
        if is_duplicate:
            checks.append(
                Check(
                    name="duplicate",
                    status=CheckStatus.FAIL,
                    detail="Faktura juz zaksiegowana (numer + sprzedawca)",
                )
            )
        else:
            checks.append(Check(name="duplicate", status=CheckStatus.PASS))

    return ValidationResult(checks=checks, is_duplicate=is_duplicate)
```

- [ ] **Step 4: Run tests to verify pass** — `uv run pytest tests/unit/test_validation_duplicates.py -v` → PASS (3). Then `uv run pytest tests/unit/test_validation.py -v` → existing 15 still PASS (no-arg calls unaffected; the all-pass test still sees exactly {nip,sums,lines} because no ledger is passed).

- [ ] **Step 5: Lint + commit**
```bash
cd /Users/mski/Developer/Invoicer && uv run ruff check . && uv run ruff format --check .
git add src/invoicer/validation.py tests/unit/test_validation_duplicates.py
git commit -m "feat: duplicate detection in validate_invoice via injected ledger"
```

---

## Task 8: Lint + pełny suite (zielona baza)

**Files:** (kontrola jakości całości)

- [ ] **Step 1: Ruff lint** — `cd /Users/mski/Developer/Invoicer && uv run ruff check .` → `All checks passed!` (lub `--fix`, potem ponów).

- [ ] **Step 2: Ruff format** — `uv run ruff format --check .` → wszystkie pliki sformatowane (lub `uv run ruff format .`, potem commit).

- [ ] **Step 3: Pełny suite** — `uv run pytest -q` → wszystko zielone. Oczekiwany przyrost: Plan 01 = 20, Plan 02 dodaje 3+3+4+4+3+3+3 = 23 → razem **43 testy** (zweryfikuj rzeczywistą liczbę i zaraportuj).

- [ ] **Step 4: Commit (jeśli ruff coś zmienił)**
```bash
cd /Users/mski/Developer/Invoicer && git add -A && git commit -m "chore: ruff clean, green suite (Plan 02 ports+ledger done)" || echo "nic do commita"
```

---

## Self-Review (wykonane przy pisaniu planu)

**Spec coverage (Plan 02 = Kamień 2; sekcje 3, 5, 6):**
- Porty `EmailSource`/`AccountingSink` (sek. 3) → Task 3 ✓
- Adapter mock `FixtureSource` (`EmailSource`) → Task 5 ✓
- Adapter mock `MockSubiektSink` (`AccountingSink`) → Task 6 ✓
- `BookingPayload` (sek. 5) + mapper → Task 2 ✓
- `Ledger` append-only (sek. 11 layout) → Task 4 ✓
- Wykrywanie duplikatów + `ValidationResult.is_duplicate` (sek. 5, 6) → Task 1 (pole) + Task 7 (logika) ✓
- `InvoiceDocument` (wejście do `extract` w Planie 03) → Task 1 ✓
- **Świadomie poza Planem 02:** `HumanReview` (port) → Plan 03 (graf + HITL); `AuditRecord` + hash-chaining → Plan 05 (bezpieczeństwo); realny Gmail/Sfera → Plany 06+. Odnotowane.

**Placeholder scan:** brak TBD/TODO; każdy krok ma pełny kod, komendy i oczekiwany wynik.

**Type consistency:** sygnatury spójne między zadaniami — `InvoiceDocument(sender, received_at, filename, content, subject="")`; `BookingPayload`/`BookingResult(booking_id, sink, status="posted")`; `invoice_to_booking_payload(invoice, treatment=None)`; `Ledger(path)` z `append`/`entries`/`is_duplicate(number, seller_nip, seller_name)`; `LedgerEntry(number, seller_name, total_gross, booking_id, booked_at, seller_nip=None)`; `validate_invoice(invoice, ledger=None)`; check o nazwie `"duplicate"`. `MockSubiektSink.post` zwraca `booking_id == f"MOCK-{number}"` (zgodne z testem `MOCK-FV/2026/06/01`). Importy pierwszej-strony w isort: `ledger` przed `models`.

**Uwaga wykonawcza:** Task 7 zmienia sygnaturę `validate_invoice` — istniejące wywołania z Planu 01 są jednoargumentowe, więc działają dalej (`ledger` ma default `None`), a test all-pass nadal widzi dokładnie `{nip, sums, lines}`.
