# Invoicer — Plan: redakcja PII w ścieżce logów (§9) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Wpiąć istniejące `redact_pii` w ścieżkę logowania przez centralny `RedactingFilter` (root-level), rozszerzyć je o realne formaty PL (IBAN/separowany NIP/grupowane konto) i pokryć testami — tak, by żaden log invoicera (w tym przyszły realny Subiekt) nie wyciekał PII.

**Architecture:** Filtr logowania `RedactingFilter(logging.Filter)` redaguje `record.getMessage()` (łapie PII również z `record.args`, jak loguje `MockSubiektSink`). Idempotentny helper `install_redaction()` podpina go na poziomie root-handlera — uniwersalnie, adapter-agnostycznie, nie do obejścia/zapomnienia. `redact_pii` pozostaje czystą funkcją (idempotentną), rozszerzoną o formaty PL ze specyficznymi grupowaniami (anti-false-positive na daty ISO). `MockSubiektSink` (i przyszły `SubiektSferaSink`) — nietknięte; pokrywa je filtr.

**Tech Stack:** Python 3.12, uv, `logging`/`re` (stdlib), Streamlit (wpięcie), pytest, ruff.

**Spec:** `docs/superpowers/specs/2026-06-21-redact-pii-logging-design.md`.

**Stan wyjściowy:** `main` po Planach 01–08. `src/invoicer/security.py` ma `redact_pii(text)` (26-cyfr konto, 10-cyfr NIP, email). `MockSubiektSink.post` (`src/invoicer/adapters/mock_subiekt.py:20`) loguje przez `logging.getLogger("invoicer.mock_subiekt").info("...sprzedawca=%s brutto=%s...", payload.number, payload.seller.name, ...)` — PII w `record.args`. Brak centralnej konfiguracji logowania. **Gałąź `feat/redact-pii-logging` już utworzona; spec scommitowany.** Baseline: 133 passed, 3 skipped, ruff czysty. Komendy `uv run`. Importy na górze.

---

## File Structure

| Plik | Odpowiedzialność |
|------|------------------|
| `src/invoicer/security.py` (MOD) | + regexy PL (IBAN/separowany NIP/grupowane konto) w `redact_pii`; + `RedactingFilter`; + `install_redaction`. |
| `tests/unit/test_security.py` (MOD) | + testy nowych formatów + brak false-positive (daty) + idempotencja. |
| `tests/unit/test_logging_redaction.py` (NEW) | `RedactingFilter` (PII w args), `install_redaction` (idempotencja, dodanie handlera, child-logger), integracja z `MockSubiektSink`. |
| `src/invoicer/ui/streamlit_app.py` (MOD) | wywołanie `install_redaction()` przy starcie. |

---

## Task 0: Gałąź + baseline

- [ ] **Step 1** — Gałąź `feat/redact-pii-logging` jest już utworzona (spec scommitowany). Potwierdź: `cd /Users/mski/Developer/Invoicer && git branch --show-current` → `feat/redact-pii-logging`.
- [ ] **Step 2: Baseline** — `uv run pytest -q` → zapisz liczbę (oczekiwane `133 passed, 3 skipped`); licz przyrosty od niej. `uv run ruff check .` → clean.

---

## Task 1: Rozszerzenie `redact_pii` o formaty PL

**Files:**
- Modify: `src/invoicer/security.py`
- Test: `tests/unit/test_security.py`

- [ ] **Step 1: Add failing tests** — APPEND do `tests/unit/test_security.py` (plik importuje już `from invoicer.security import redact_pii`):
```python
def test_redacts_pl_iban_grouped():
    out = redact_pii("przelew PL61 1090 1014 0000 0712 1981 2874 dzis")
    assert "[KONTO]" in out
    assert "1090" not in out


def test_redacts_account_grouped_without_prefix():
    out = redact_pii("konto 61 1090 1014 0000 0712 1981 2874")
    assert "[KONTO]" in out
    assert "1090" not in out


def test_redacts_pl_iban_compact():
    out = redact_pii("IBAN PL61109010140000071219812874 koniec")
    assert "[KONTO]" in out
    assert "6110901014" not in out


def test_redacts_nip_with_separators():
    assert redact_pii("NIP 526-000-12-46") == "NIP [NIP]"


def test_does_not_redact_iso_date_or_time():
    s = "2026-06-01 10:00:00 faktura VAT 23%"
    assert redact_pii(s) == s


def test_redact_pii_is_idempotent():
    s = "NIP 5260001246, konto 61109010140000071219812874, mail a@b.pl"
    once = redact_pii(s)
    assert redact_pii(once) == once
```

- [ ] **Step 2: Run to verify it fails** — `uv run pytest tests/unit/test_security.py -k "iban or grouped or separators or iso or idempotent" -v` → FAIL (np. `test_redacts_pl_iban_grouped` — obecny `redact_pii` nie maskuje grupowanego IBAN; `[KONTO]` nieobecne).

- [ ] **Step 3: Implement** — zastąp ZAWARTOŚĆ `src/invoicer/security.py` (sekcja regexów + `redact_pii`) tak, by plik wyglądał DOKŁADNIE:
```python
from __future__ import annotations

import re

# Konto / IBAN — od najdluzszych/najbardziej specyficznych (kolejnosc ma znaczenie):
_IBAN_GROUPED = re.compile(r"\b(?:[Pp][Ll])?\d{2}(?:[ ]\d{4}){6}\b")  # NRB/IBAN w grupach po 4
_IBAN_PL = re.compile(r"\b[Pp][Ll]\d{26}\b")  # IBAN PL zwarty (z prefiksem PL)
_ACCOUNT = re.compile(r"\b\d{26}\b")  # NRB zwarty (26 cyfr)

# NIP:
_NIP_SEP = re.compile(r"\b\d{3}-\d{3}-\d{2}-\d{2}\b|\b\d{3}-\d{2}-\d{2}-\d{3}\b")  # NIP z myslnikami
_NIP = re.compile(r"\b\d{10}\b")  # NIP zwarty (10 cyfr)

# E-mail:
_EMAIL = re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b")


def redact_pii(text: str) -> str:
    """Maskuje dane wrazliwe (rachunek/IBAN, NIP, e-mail) w tekscie przeznaczonym do logow.

    Spec sek. 9: kroki rozumujace / logi nie powinny wyciekac PII. Kolejnosc: konto/IBAN
    (grupowane -> z prefiksem PL -> zwarte) przed NIP (z separatorami -> zwarty), na koncu
    e-mail. Specyficzne grupowania (nie generyczne "cyfry+separatory") nie lapia dat ISO.
    Funkcja jest idempotentna: redact_pii(redact_pii(x)) == redact_pii(x).
    """
    text = _IBAN_GROUPED.sub("[KONTO]", text)
    text = _IBAN_PL.sub("[KONTO]", text)
    text = _ACCOUNT.sub("[KONTO]", text)
    text = _NIP_SEP.sub("[NIP]", text)
    text = _NIP.sub("[NIP]", text)
    return _EMAIL.sub("[EMAIL]", text)
```
(Uwaga: NIE dodawaj `RedactingFilter`/`install_redaction` w tym tasku — to Task 2.)

- [ ] **Step 4: Verify pass** — `uv run pytest tests/unit/test_security.py -v` → PASS (5 istniejących + 6 nowych = 11). `uv run pytest -q` → green (baza 133 + 6 = 139, 3 skipped). `uv run ruff check . && uv run ruff format --check .` → clean.

- [ ] **Step 5: Commit**
```bash
git add src/invoicer/security.py tests/unit/test_security.py
git commit -m "feat: extend redact_pii with PL IBAN / separated NIP / grouped account (no false-positive on ISO dates)"
```

---

## Task 2: `RedactingFilter` + `install_redaction` + testy

**Files:**
- Modify: `src/invoicer/security.py`
- Test: `tests/unit/test_logging_redaction.py` (NEW)

- [ ] **Step 1: Write the failing tests** — utwórz `tests/unit/test_logging_redaction.py`:
```python
import logging
from decimal import Decimal

from invoicer.adapters.mock_subiekt import MockSubiektSink
from invoicer.booking import BookingPayload
from invoicer.models import Party
from invoicer.security import RedactingFilter, install_redaction


def _capturing_handler() -> tuple[logging.Handler, list[str]]:
    """Handler zbierajacy sformatowane linie do listy (deterministyczny, bez I/O)."""
    lines: list[str] = []

    class _ListHandler(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            lines.append(self.format(record))

    handler = _ListHandler()
    handler.setFormatter(logging.Formatter("%(message)s"))
    return handler, lines


def _payload_with_pii() -> BookingPayload:
    # nazwa sprzedawcy zawiera e-mail (realny przypadek: kontakt z naglowka faktury)
    return BookingPayload(
        seller=Party(name="ACME ksiegowa@firma.pl", nip="5260001246", country="PL"),
        buyer=Party(name="Klient", country="PL"),
        number="FV/1",
        currency="PLN",
        lines=[],
        total_net=Decimal("1000.00"),
        total_vat=Decimal("230.00"),
        total_gross=Decimal("1230.00"),
        treatment="krajowa",
    )


def test_redacting_filter_masks_pii_from_record_args():
    f = RedactingFilter()
    record = logging.LogRecord(
        name="invoicer.test",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="sprzedawca=%s nip=%s",
        args=("ACME ksiegowa@klient.pl", "5260001246"),
        exc_info=None,
    )
    assert f.filter(record) is True
    out = record.getMessage()
    assert "ksiegowa@klient.pl" not in out
    assert "5260001246" not in out
    assert "[EMAIL]" in out
    assert "[NIP]" in out


def test_install_redaction_is_idempotent():
    logger = logging.getLogger("invoicer.test_idem")
    handler, _ = _capturing_handler()
    logger.addHandler(handler)
    try:
        install_redaction(logger)
        install_redaction(logger)
        n = sum(isinstance(flt, RedactingFilter) for flt in handler.filters)
        assert n == 1
    finally:
        logger.removeHandler(handler)


def test_install_redaction_adds_handler_when_none():
    logger = logging.getLogger("invoicer.test_nohandler")
    logger.handlers.clear()
    try:
        install_redaction(logger)
        assert logger.handlers  # dodano handler
        assert any(
            isinstance(flt, RedactingFilter) for h in logger.handlers for flt in h.filters
        )
    finally:
        logger.handlers.clear()


def test_install_redaction_redacts_child_logger_output():
    parent = logging.getLogger("invoicer.test_parent")
    handler, lines = _capturing_handler()
    parent.addHandler(handler)
    parent.setLevel(logging.INFO)
    old_propagate = parent.propagate
    parent.propagate = False  # izolacja: tylko nasz handler
    install_redaction(parent)
    try:
        child = logging.getLogger("invoicer.test_parent.child")
        child.info("nip=%s", "5260001246")  # propaguje do handlera parenta
        assert lines == ["nip=[NIP]"]
    finally:
        parent.removeHandler(handler)
        parent.propagate = old_propagate


def test_mock_subiekt_log_is_redacted_after_install():
    parent = logging.getLogger("invoicer")
    handler, lines = _capturing_handler()
    parent.addHandler(handler)
    parent.setLevel(logging.INFO)
    old_propagate = parent.propagate
    parent.propagate = False
    install_redaction(parent)
    try:
        MockSubiektSink().post(_payload_with_pii())
        joined = "\n".join(lines)
        assert "ksiegowa@firma.pl" not in joined
        assert "[EMAIL]" in joined
    finally:
        parent.removeHandler(handler)
        parent.propagate = old_propagate
```

- [ ] **Step 2: Run to verify it fails** — `uv run pytest tests/unit/test_logging_redaction.py -v` → FAIL (`ImportError: cannot import name 'RedactingFilter' from 'invoicer.security'`).

- [ ] **Step 3: Implement** — APPEND do `src/invoicer/security.py` (dodaj `import logging` na górze w sekcji stdlib — kolejność isort: `import logging` przed `import re`):
```python
class RedactingFilter(logging.Filter):
    """Filtr logowania, ktory maskuje PII w finalnej tresci rekordu.

    Operuje na `record.getMessage()` (renderuje %s-argumenty PRZED redakcja), wiec lapie
    PII przekazane przez `record.args` (tak loguje MockSubiektSink). Po redakcji czysci
    `args`, by handlery nie re-renderowaly surowych wartosci. Zawsze przepuszcza rekord
    (filtr transformujacy, nie odrzucajacy). Bezpieczny przy wielu handlerach dzieki
    idempotencji `redact_pii`.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = redact_pii(record.getMessage())
        record.args = ()
        return True


def install_redaction(logger: logging.Logger | None = None) -> None:
    """Podpina RedactingFilter do handlerow loggera (domyslnie root) — idempotentnie.

    Redaguje WSZYSTKIE rekordy docierajace do handlera (rowniez child-loggery `invoicer.*`
    oraz przyszly SubiektSferaSink i logi third-party — over-masking jest bezpieczny).
    Jesli cel nie ma handlera, dodaje StreamHandler. Wolaj PO konfiguracji logowania aplikacji.
    """
    target = logger if logger is not None else logging.getLogger()
    if not target.handlers:
        target.addHandler(logging.StreamHandler())
    for handler in target.handlers:
        if not any(isinstance(flt, RedactingFilter) for flt in handler.filters):
            handler.addFilter(RedactingFilter())
```

- [ ] **Step 4: Verify pass** — `uv run pytest tests/unit/test_logging_redaction.py -v` → PASS (5). `uv run pytest -q` → green (139 + 5 = 144, 3 skipped). `uv run ruff check . && uv run ruff format --check .` → clean.

- [ ] **Step 5: Commit**
```bash
git add src/invoicer/security.py tests/unit/test_logging_redaction.py
git commit -m "feat: RedactingFilter + install_redaction (root-level log PII scrubbing, covers all adapters)"
```

---

## Task 3: Wpięcie `install_redaction()` w Streamlit

**Files:**
- Modify: `src/invoicer/ui/streamlit_app.py`

> Plik Streamlit to cienka prezentacja — NIE testowany jednostkowo (runtime Streamlit). `install_redaction` jest pokryte w `test_logging_redaction.py`. `install_redaction()` jest idempotentny, więc wywołanie przy każdym rerunie Streamlit jest bezpieczne.

- [ ] **Step 1: Dodaj import + wywołanie** — w `src/invoicer/ui/streamlit_app.py`:
  - W bloku importów (po `from invoicer.runner import (...)`) dodaj linię: `from invoicer.security import install_redaction` (isort: `invoicer.runner` przed `invoicer.security`).
  - PO bloku importów, a PRZED `st.set_page_config(...)`, dodaj:
```python
install_redaction()  # scrubuje PII ze wszystkich logow invoicera (idempotentne)
```

- [ ] **Step 2: Sanity (nie uruchamiamy UI)** — `uv run python -c "import ast; ast.parse(open('src/invoicer/ui/streamlit_app.py').read()); print('parse ok')"` → `parse ok`. `uv run ruff check src/invoicer/ui/ && uv run ruff format --check src/invoicer/ui/` → clean. `uv run pytest -q` → bez zmian (144 passed, 3 skipped — plik Streamlit nieimportowany przez testy).

- [ ] **Step 3: Commit**
```bash
git add src/invoicer/ui/streamlit_app.py
git commit -m "feat: install log PII redaction on Streamlit startup"
```

---

## Task 4: Lint + pełny suite (zielona baza)

- [ ] **Step 1: Ruff** — `cd /Users/mski/Developer/Invoicer && uv run ruff check . && uv run ruff format --check .` → clean.
- [ ] **Step 2: Pełny suite** — `uv run pytest -q` → **144 passed, 3 skipped** (zweryfikuj realny licznik: baza 133 + 6 Task1 + 5 Task2 = 144).
- [ ] **Step 3: Commit porządkowy (jeśli ruff coś zmienił)** — `git add -A && git commit -m "chore: ruff clean, green suite (redact_pii logging done)" || echo "nic do commita"`.

---

## Self-Review (wykonane przy pisaniu planu)

**Spec coverage:**
- Rozszerzenie `redact_pii` (PL IBAN / separowany NIP / grupowane konto) — spec §3.1 → Task 1 ✓
- Brak false-positive na datach ISO + idempotencja — spec §3.1 → Task 1 (`test_does_not_redact_iso_date_or_time`, `test_redact_pii_is_idempotent`) ✓
- `RedactingFilter` (PII z `record.args`) — spec §3.2 → Task 2 ✓
- `install_redaction` (root-level, idempotentny, dodanie handlera, child-logger) — spec §3.3 → Task 2 ✓
- Integracja z realnym `MockSubiektSink` — spec §5 → Task 2 (`test_mock_subiekt_log_is_redacted_after_install`) ✓
- Wpięcie w Streamlit — spec §3.4 → Task 3 ✓
- Real `SubiektSferaSink` — świadomie POZA zakresem (spec §2); pokryty automatycznie przez root-level filtr.

**Placeholder scan:** brak TBD/TODO; pełny kod + komendy.

**Type consistency:** `redact_pii(text: str) -> str` (idempotentny); `RedactingFilter(logging.Filter).filter(record) -> bool`; `install_redaction(logger: logging.Logger | None = None) -> None`. Testy używają `Party`/`BookingPayload` o sygnaturach z `models.py`/`booking.py` (zweryfikowane: `BookingPayload(seller, buyer, number, currency, lines, total_net, total_vat, total_gross, treatment=None)`, `Party(name, country, nip=None, vat_id=None)`). Logger `MockSubiektSink` = `"invoicer.mock_subiekt"` (child `invoicer`) — łapany przez filtr na handlerze parenta `invoicer`/root.

**Uwaga wykonawcza:** filtr na HANDLERZE (nie na loggerze) łapie rekordy z child-loggerów propagujące w górę — dlatego testy podpinają handler do parenta `invoicer`/dedykowanego loggera i sprawdzają child. `install_redaction` modyfikuje stan globalny tylko gdy wołany z domyślnym root (w testach przekazujemy dedykowane loggery i sprzątamy w `finally`). Idempotencja `redact_pii` gwarantuje bezpieczeństwo wielokrotnego filtrowania (wiele handlerów / rerun Streamlit).
