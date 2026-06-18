# Invoicer — Plan 01: Foundations Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Postawić szkielet projektu Invoicer (uv) oraz w pełni przetestowany rdzeń: modele danych faktury (Pydantic v2) i bibliotekę walidacji podatkowej (suma kontrolna NIP, zgodność sum, kompletność).

**Architecture:** Layout `src/` z pakietem `invoicer`. Czyste, bezstanowe funkcje walidacji nad modelami Pydantic — łatwe w TDD, bez zależności od I/O ani LLM. To fundament, na którym kolejne plany budują graf, porty i bezpieczeństwo.

**Tech Stack:** Python 3.12, uv (zarządzanie projektem/venv), Pydantic v2, pytest, ruff. Kwoty jako `Decimal` (poprawność groszowa).

**Spec:** `docs/superpowers/specs/2026-06-18-invoicer-design.md` — ten plan realizuje Kamień milowy 1 oraz sekcje 5 (modele) i 6 (walidacja).

---

## File Structure

| Plik | Odpowiedzialność |
|------|------------------|
| `pyproject.toml` | Definicja projektu uv: zależności, konfiguracja ruff/pytest, layout `src/`. |
| `.gitignore` | Wykluczenia (venv, sekrety, cache). |
| `.env.example` | Szablon zmiennych środowiskowych (bez prawdziwych sekretów). |
| `src/invoicer/__init__.py` | Marker pakietu. |
| `src/invoicer/models.py` | Modele domeny: `Party`, `LineItem`, `Invoice`, `Check`, `CheckStatus`, `ValidationResult`. |
| `src/invoicer/validation.py` | Czyste funkcje walidacji: `nip_checksum_valid`, `totals_consistent`, `validate_invoice`. |
| `tests/unit/test_models.py` | Testy konstrukcji/serializacji modeli. |
| `tests/unit/test_validation.py` | Testy walidacji (NIP, sumy, agregator). |

---

## Task 1: Scaffold projektu uv

**Files:**
- Create: `pyproject.toml`
- Create: `.gitignore`
- Create: `.env.example`
- Create: `src/invoicer/__init__.py`
- Create: `tests/unit/test_smoke.py`

- [ ] **Step 1: Utwórz `pyproject.toml`**

```toml
[project]
name = "invoicer"
version = "0.1.0"
description = "Agentowy asystent ksiegowy — pobiera, waliduje i klasyfikuje faktury (PL tax), z bramka human-in-the-loop."
requires-python = ">=3.12"
dependencies = [
    "pydantic>=2.7",
]

[dependency-groups]
dev = [
    "pytest>=8.0",
    "ruff>=0.5",
]

[tool.uv]
package = false

[tool.ruff]
line-length = 100

[tool.ruff.lint]
select = ["E", "F", "I", "UP", "B"]

[tool.pytest.ini_options]
pythonpath = ["src"]
testpaths = ["tests"]
```

- [ ] **Step 2: Utwórz `.gitignore` i `.env.example`**

`.gitignore`:
```gitignore
.venv/
__pycache__/
*.pyc
.pytest_cache/
.ruff_cache/
.env
*.sqlite
.DS_Store
```

`.env.example`:
```dotenv
# Szablon — skopiuj do .env (które jest w .gitignore). Nie wpisuj prawdziwych sekretow tutaj.
ANTHROPIC_API_KEY=
GMAIL_SENDER_FILTER=
```

- [ ] **Step 3: Utwórz pakiet i test smoke**

`src/invoicer/__init__.py`:
```python
__version__ = "0.1.0"
```

`tests/unit/test_smoke.py`:
```python
import invoicer


def test_package_imports():
    assert invoicer.__version__ == "0.1.0"
```

- [ ] **Step 4: Zsynchronizuj środowisko**

Run: `cd /Users/mski/Developer/Invoicer && uv sync`
Expected: tworzy `.venv`, instaluje pydantic + dev (pytest, ruff); kończy się bez błędu.

- [ ] **Step 5: Uruchom test smoke (ma przejść)**

Run: `uv run pytest tests/unit/test_smoke.py -v`
Expected: PASS (`test_package_imports`).

- [ ] **Step 6: Commit**

```bash
git add pyproject.toml .gitignore .env.example src/ tests/ uv.lock
git commit -m "feat: scaffold Invoicer (uv, src layout, smoke test)"
```

---

## Task 2: Modele domeny (Pydantic v2)

**Files:**
- Create: `src/invoicer/models.py`
- Test: `tests/unit/test_models.py`

- [ ] **Step 1: Napisz failing test**

`tests/unit/test_models.py`:
```python
from datetime import date
from decimal import Decimal

from invoicer.models import (
    Check,
    CheckStatus,
    Invoice,
    LineItem,
    Party,
    ValidationResult,
)


def _sample_invoice() -> Invoice:
    line = LineItem(
        description="Usluga programistyczna",
        quantity=Decimal("1"),
        unit_net=Decimal("1000.00"),
        vat_rate=Decimal("0.23"),
        net=Decimal("1000.00"),
        vat=Decimal("230.00"),
        gross=Decimal("1230.00"),
    )
    return Invoice(
        seller=Party(name="ACME sp. z o.o.", nip="5260001246", country="PL"),
        buyer=Party(name="Klient sp. z o.o.", nip="1234563218", country="PL"),
        number="FV/2026/06/01",
        issue_date=date(2026, 6, 1),
        currency="PLN",
        lines=[line],
        total_net=Decimal("1000.00"),
        total_vat=Decimal("230.00"),
        total_gross=Decimal("1230.00"),
    )


def test_invoice_builds_and_holds_values():
    inv = _sample_invoice()
    assert inv.seller.country == "PL"
    assert inv.lines[0].gross == Decimal("1230.00")
    assert inv.total_gross == Decimal("1230.00")


def test_party_defaults_country_pl_and_optional_nip():
    p = Party(name="Foreign Ltd", country="GB")
    assert p.nip is None
    assert p.country == "GB"


def test_validation_result_partitions_checks():
    vr = ValidationResult(
        checks=[
            Check(name="nip", status=CheckStatus.PASS),
            Check(name="sums", status=CheckStatus.FAIL, detail="niespojne"),
            Check(name="lines", status=CheckStatus.WARN, detail="ostrzezenie"),
        ]
    )
    assert vr.ok is False
    assert [c.name for c in vr.hard_errors] == ["sums"]
    assert [c.name for c in vr.soft_flags] == ["lines"]
```

- [ ] **Step 2: Uruchom test (ma się wywalić)**

Run: `uv run pytest tests/unit/test_models.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'invoicer.models'`.

- [ ] **Step 3: Zaimplementuj `src/invoicer/models.py`**

```python
from __future__ import annotations

from datetime import date
from decimal import Decimal
from enum import Enum

from pydantic import BaseModel


class Party(BaseModel):
    name: str
    nip: str | None = None
    country: str = "PL"  # ISO-2
    address: str | None = None
    vat_id: str | None = None


class LineItem(BaseModel):
    description: str
    quantity: Decimal
    unit_net: Decimal
    vat_rate: Decimal  # np. Decimal("0.23")
    net: Decimal
    vat: Decimal
    gross: Decimal


class Invoice(BaseModel):
    seller: Party
    buyer: Party
    number: str
    issue_date: date
    sale_date: date | None = None
    due_date: date | None = None
    currency: str = "PLN"
    lines: list[LineItem]
    total_net: Decimal
    total_vat: Decimal
    total_gross: Decimal
    extraction_confidence: float | None = None


class CheckStatus(str, Enum):
    PASS = "pass"
    WARN = "warn"
    FAIL = "fail"


class Check(BaseModel):
    name: str
    status: CheckStatus
    detail: str = ""


class ValidationResult(BaseModel):
    checks: list[Check]

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

- [ ] **Step 4: Uruchom test (ma przejść)**

Run: `uv run pytest tests/unit/test_models.py -v`
Expected: PASS (3 testy).

- [ ] **Step 5: Commit**

```bash
git add src/invoicer/models.py tests/unit/test_models.py
git commit -m "feat: domain models (Invoice, Party, LineItem, ValidationResult)"
```

---

## Task 3: Walidacja NIP (suma kontrolna)

**Files:**
- Create: `src/invoicer/validation.py`
- Test: `tests/unit/test_validation.py`

- [ ] **Step 1: Napisz failing test**

`tests/unit/test_validation.py`:
```python
from invoicer.validation import nip_checksum_valid


def test_valid_nip_plain():
    assert nip_checksum_valid("5260001246") is True


def test_valid_nip_with_formatting():
    assert nip_checksum_valid("526-000-12-46") is True


def test_invalid_nip_bad_checksum():
    assert nip_checksum_valid("5260001247") is False


def test_invalid_nip_wrong_length():
    assert nip_checksum_valid("12345") is False


def test_invalid_nip_control_equals_ten():
    # Pierwsze 9 cyfr daje sume wazona ≡ 10 mod 11 → NIP niepoprawny z definicji.
    assert nip_checksum_valid("9000000001") is False


def test_none_nip_is_invalid():
    assert nip_checksum_valid(None) is False
```

- [ ] **Step 2: Uruchom test (ma się wywalić)**

Run: `uv run pytest tests/unit/test_validation.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'invoicer.validation'`.

- [ ] **Step 3: Zaimplementuj funkcję NIP w `src/invoicer/validation.py`**

```python
from __future__ import annotations

NIP_WEIGHTS = (6, 5, 7, 2, 3, 4, 5, 6, 7)


def _digits_only(value: str) -> str:
    return "".join(ch for ch in value if ch.isdigit())


def nip_checksum_valid(nip: str | None) -> bool:
    """Walidacja polskiego NIP algorytmem wagowym (mod 11).

    Suma kontrolna == 10 oznacza NIP niepoprawny (cyfra kontrolna nie moze byc 10).
    """
    if not nip:
        return False
    digits = _digits_only(nip)
    if len(digits) != 10:
        return False
    weighted = sum(int(digits[i]) * NIP_WEIGHTS[i] for i in range(9))
    control = weighted % 11
    if control == 10:
        return False
    return control == int(digits[9])
```

- [ ] **Step 4: Uruchom test (ma przejść)**

Run: `uv run pytest tests/unit/test_validation.py -v`
Expected: PASS (6 testów).

- [ ] **Step 5: Commit**

```bash
git add src/invoicer/validation.py tests/unit/test_validation.py
git commit -m "feat: NIP checksum validation (mod 11)"
```

---

## Task 4: Zgodność sum (netto+VAT=brutto, Σ pozycji)

**Files:**
- Modify: `src/invoicer/validation.py`
- Test: `tests/unit/test_validation.py`

- [ ] **Step 1: Dopisz failing test**

Dodaj na końcu `tests/unit/test_validation.py`:
```python
from datetime import date
from decimal import Decimal

from invoicer.models import Invoice, LineItem, Party
from invoicer.validation import totals_consistent


def _invoice(total_net, total_vat, total_gross) -> Invoice:
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
        lines=[line],
        total_net=Decimal(total_net),
        total_vat=Decimal(total_vat),
        total_gross=Decimal(total_gross),
    )


def test_totals_consistent_true():
    assert totals_consistent(_invoice("1000.00", "230.00", "1230.00")) is True


def test_totals_within_grosz_tolerance():
    assert totals_consistent(_invoice("1000.00", "230.00", "1230.01")) is True


def test_totals_inconsistent_gross():
    assert totals_consistent(_invoice("1000.00", "230.00", "1300.00")) is False


def test_totals_inconsistent_with_lines_sum():
    assert totals_consistent(_invoice("999.00", "230.00", "1229.00")) is False
```

- [ ] **Step 2: Uruchom nowe testy (mają się wywalić)**

Run: `uv run pytest tests/unit/test_validation.py -k totals -v`
Expected: FAIL — `ImportError: cannot import name 'totals_consistent'`.

- [ ] **Step 3: Dopisz `totals_consistent` do `src/invoicer/validation.py`**

Dodaj import i funkcję (na górze pliku, pod istniejącym `from __future__`):
```python
from decimal import Decimal

from invoicer.models import Invoice
```

Na końcu pliku:
```python
_CENT = Decimal("0.01")


def totals_consistent(invoice: Invoice) -> bool:
    """Sprawdza netto+VAT=brutto globalnie oraz zgodnosc sum pozycji z naglowkiem.

    Tolerancja groszowa na zaokraglenia.
    """
    sum_net = sum((line.net for line in invoice.lines), Decimal("0"))
    sum_vat = sum((line.vat for line in invoice.lines), Decimal("0"))
    sum_gross = sum((line.gross for line in invoice.lines), Decimal("0"))
    return (
        abs(sum_net - invoice.total_net) <= _CENT
        and abs(sum_vat - invoice.total_vat) <= _CENT
        and abs(sum_gross - invoice.total_gross) <= _CENT
        and abs((invoice.total_net + invoice.total_vat) - invoice.total_gross) <= _CENT
    )
```

- [ ] **Step 4: Uruchom testy (mają przejść)**

Run: `uv run pytest tests/unit/test_validation.py -v`
Expected: PASS (10 testów: 6 NIP + 4 sumy).

- [ ] **Step 5: Commit**

```bash
git add src/invoicer/validation.py tests/unit/test_validation.py
git commit -m "feat: totals consistency check (netto+VAT=brutto, line sums)"
```

---

## Task 5: Agregator `validate_invoice`

**Files:**
- Modify: `src/invoicer/validation.py`
- Test: `tests/unit/test_validation.py`

- [ ] **Step 1: Dopisz failing test**

Dodaj na końcu `tests/unit/test_validation.py`:
```python
from invoicer.models import CheckStatus
from invoicer.validation import validate_invoice


def test_validate_invoice_all_pass():
    vr = validate_invoice(_invoice("1000.00", "230.00", "1230.00"))
    assert vr.ok is True
    assert {c.name for c in vr.checks} == {"nip", "sums", "lines"}


def test_validate_invoice_bad_nip_fails():
    inv = _invoice("1000.00", "230.00", "1230.00")
    inv.seller.nip = "5260001247"  # zla suma kontrolna
    vr = validate_invoice(inv)
    assert vr.ok is False
    nip_check = next(c for c in vr.checks if c.name == "nip")
    assert nip_check.status == CheckStatus.FAIL


def test_validate_invoice_foreign_seller_nip_warn():
    inv = _invoice("1000.00", "230.00", "1230.00")
    inv.seller.country = "GB"
    inv.seller.nip = None
    vr = validate_invoice(inv)
    nip_check = next(c for c in vr.checks if c.name == "nip")
    assert nip_check.status == CheckStatus.WARN
    assert vr.ok is True  # zagraniczny brak NIP nie jest twardym bledem


def test_validate_invoice_inconsistent_sums_fails():
    vr = validate_invoice(_invoice("1000.00", "230.00", "1300.00"))
    assert vr.ok is False
    sums_check = next(c for c in vr.checks if c.name == "sums")
    assert sums_check.status == CheckStatus.FAIL
```

- [ ] **Step 2: Uruchom nowe testy (mają się wywalić)**

Run: `uv run pytest tests/unit/test_validation.py -k validate_invoice -v`
Expected: FAIL — `ImportError: cannot import name 'validate_invoice'`.

- [ ] **Step 3: Dopisz `validate_invoice` do `src/invoicer/validation.py`**

Rozszerz import modeli na górze pliku:
```python
from invoicer.models import Check, CheckStatus, Invoice, ValidationResult
```

Na końcu pliku:
```python
def validate_invoice(invoice: Invoice) -> ValidationResult:
    """Łączy kontrole deterministyczne w jeden ValidationResult.

    NIP wymagany tylko dla sprzedawcy z PL; zagraniczny → WARN (nie FAIL).
    Duplikaty dochodza w Planie 02 (potrzebuja ledger).
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

    return ValidationResult(checks=checks)
```

- [ ] **Step 4: Uruchom cały zestaw walidacji (ma przejść)**

Run: `uv run pytest tests/unit/test_validation.py -v`
Expected: PASS (14 testów).

- [ ] **Step 5: Commit**

```bash
git add src/invoicer/validation.py tests/unit/test_validation.py
git commit -m "feat: validate_invoice aggregator (NIP + sums + lines)"
```

---

## Task 6: Lint + pełny zestaw testów (zielona baza)

**Files:**
- (brak nowych — kontrola jakości całości)

- [ ] **Step 1: Uruchom ruff (ma być czysto)**

Run: `uv run ruff check .`
Expected: `All checks passed!` (lub auto-fix: `uv run ruff check . --fix`, potem ponów).

- [ ] **Step 2: Uruchom format check**

Run: `uv run ruff format --check .`
Expected: brak plików do przeformatowania (lub `uv run ruff format .`, potem commit).

- [ ] **Step 3: Uruchom pełny zestaw testów**

Run: `uv run pytest -q`
Expected: PASS — 18 testów (1 smoke + 3 modele + 14 walidacja), 0 błędów.

- [ ] **Step 4: Commit (jeśli ruff coś zmienił)**

```bash
git add -A
git commit -m "chore: ruff clean, green test suite (Plan 01 foundations done)" || echo "nic do commita"
```

---

## Self-Review (wykonane przy pisaniu planu)

**Spec coverage (Plan 01 = Kamień 1, sekcje 5–6):**
- Modele `Party`/`LineItem`/`Invoice`/`ValidationResult`/`Check` → Task 2 ✓
- Suma kontrolna NIP → Task 3 ✓
- Zgodność sum (netto+VAT=brutto, Σ pozycji) → Task 4 ✓
- Kompletność / agregator `validate_invoice` → Task 5 ✓
- Wykrywanie duplikatów → **świadomie w Planie 02** (wymaga `ledger`); odnotowane w docstringu Task 5.
- `Classification`/`BookingPayload`/`AuditRecord` → Plany 02–05 (poza zakresem fundamentów).

**Placeholder scan:** brak TBD/TODO; każdy krok ma pełny kod i komendy z oczekiwanym wynikiem.

**Type consistency:** `CheckStatus`, `Check`, `ValidationResult.ok/hard_errors/soft_flags`, `nip_checksum_valid`, `totals_consistent`, `validate_invoice` użyte spójnie między Task 2–5. Stała tolerancji `_CENT`. Liczba testów rośnie spójnie: 6 → 10 → 14 (+1 smoke +3 modele = 18).

**Uwaga wykonawcza:** stałe importy modeli w `validation.py` dochodzą przyrostowo (Task 3 bez importu modeli; Task 4 dodaje `Invoice`; Task 5 rozszerza o `Check, CheckStatus, ValidationResult`). Wykonując kroki po kolei, import na górze pliku ma finalnie obejmować `Check, CheckStatus, Invoice, ValidationResult`.
