# Invoicer — Design: Gmail dzienny + detekcja faktury

**Data:** 2026-06-23
**Status:** zatwierdzony projekt (do realizacji subagent-driven, jak Plany 01–08)
**Realizuje:** zawężenie pobierania z Gmaila do bieżącego dnia + PDF, oraz odsianie nie-faktur (klasyfikator LLM) przed wejściem w pipeline.

---

## 1. Problem / kontekst

`GmailAdapter._build_query` ([gmail.py:12](../../../src/invoicer/adapters/gmail.py)) buduje `from:{sender} has:attachment filename:pdf` — **bez ograniczenia czasu i bez rozróżnienia faktura/nie-faktura**. Realny fetch pobrał ~750 PDF-ów z 15 lat (CV, ebooki), nie faktury. Wymagania użytkownika:
1. szukać tylko w mailach z **bieżącego dnia kalendarzowego** (dziś 23.06, jutro 24.06 — dynamicznie),
2. tylko pliki **.pdf**,
3. **sprawdzić, czy to faktura** — jeśli tak, kontynuować proces; jeśli nie, pominąć.

**Decyzja (z brainstormu):** detekcja faktury = **dedykowany klasyfikator Claude yes/no** za portem `InvoiceDetector` (semantycznie niezawodny, zgodny z wzorcem „LLM za portem", stub do CI; dla nie-faktur tanio odsiewa PRZED drogą ekstrakcją). Placement: **pre-filtr** — graf zostaje nietknięty (skupiony na fakturze).

**Wzorce w repo:** `ClaudeVisionExtractor` (wstrzykiwalny LLM, multimodalna wiadomość, structured output, guard prompt-injection) — `ClaudeInvoiceDetector` go naśladuje. `GmailAdapter` (wstrzykiwalny `service`, live-gated test). `build_invoice_graph(..., reasoner=IdentityReasoner())` — wzorzec stub-domyślny.

---

## 2. Zakres

**W zakresie:**
- `GmailAdapter` — dzienny filtr (`after:/before:`), `today` wstrzykiwany.
- Port `InvoiceDetector` + `ClaudeInvoiceDetector` (real) + `StubInvoiceDetector` (CI/offline).
- Orkiestrator `fetch_invoice_documents` (fetch → odsianie nie-faktur).
- Testy jednostkowe (deterministyczne) + live-gated.

**Poza zakresem (świadome YAGNI / nietykane):**
- **Graf, `build_invoice_graph`, `build_demo_graph`, Streamlit-upload** — nietknięte (detekcja dotyczy ścieżki Gmail; przy uploadzie user sam wybiera plik = faktura).
- Pełny orkiestrator „inbox → graf → bramka → księgowanie" pętlą bezobsługową (HITL wymaga człowieka) — orkiestrator zwraca odsiane faktury; karmienie ich do `start_document` to krok wołającego.
- Zmiana portu `EmailSource` (`fetch(sender)`) — `today` jest wewnętrznym parametrem `GmailAdapter`, nie przecieka do portu.

---

## 3. Architektura

### 3.1 `GmailAdapter` — dzienny filtr (`src/invoicer/adapters/gmail.py` MOD)
- `_build_query(sender: str, *, today: date) -> str` → `from:{token} after:{RRRR/MM/DD} before:{(today+1) RRRR/MM/DD} has:attachment filename:pdf`. `after` inclusive, `before` exclusive → dokładnie jeden dzień kalendarzowy.
- `GmailAdapter.fetch(sender: str, *, today: date | None = None)` → `today = today or date.today()`; przekazuje do `_build_query`. PDF-only już zapewnione (`filename:pdf` + `_iter_pdf_parts` po MIME).
- Zgodność z portem `EmailSource` zachowana (`fetch(sender)` nadal działa; `today` to keyword-only z defaultem).

### 3.2 Port `InvoiceDetector` (`src/invoicer/ports.py` MOD)
```python
@runtime_checkable
class InvoiceDetector(Protocol):
    """Klasyfikator: czy dokument to faktura (przed wejsciem w pipeline)."""
    def is_invoice(self, document: InvoiceDocument) -> bool: ...
```

### 3.3 `ClaudeInvoiceDetector` (`src/invoicer/adapters/claude_detector.py` NEW)
- Wzór jak `ClaudeVisionExtractor`: wstrzykiwalny `llm` (CI: fake; domyślnie leniwie `ChatAnthropic`).
- `is_invoice(document)`: buduje multimodalną wiadomość (prompt detekcji + dokument jako blok `file`/`image` — reuse `_mime_and_block` z `claude_extractor`), `with_structured_output(InvoiceCheck)`, zwraca `check.is_invoice`.
- `InvoiceCheck(BaseModel)`: `is_invoice: bool`, `reason: str` (krótkie uzasadnienie PL).
- Prompt: pyta „czy to faktura/rachunek?", z **guardem prompt-injection** (dokument = dane, nie instrukcje) — jak w ekstraktorze.

### 3.4 `StubInvoiceDetector` (`src/invoicer/adapters/stub_detector.py` NEW)
```python
class StubInvoiceDetector:
    def __init__(self, *, result: bool = True) -> None: self._result = result
    def is_invoice(self, document: InvoiceDocument) -> bool: return self._result
```
Deterministyczny do CI/offline; domyślnie `True`.

### 3.5 Orkiestrator (`src/invoicer/runner.py` MOD)
```python
def fetch_invoice_documents(source, detector, sender, *, ...) -> list[InvoiceDocument]:
    return [d for d in source.fetch(sender) if detector.is_invoice(d)]
```
„Kontynuuj proces" = każdą zwróconą fakturę wołający karmi przez `start_document` → bramka human_review (bez auto-approve).

---

## 4. Przepływ danych

```
GmailAdapter.fetch(sender, today=dziś)        # from:.. after:dziś before:jutro has:attachment filename:pdf
   → [PDF-y wyslane DZIS]
   → detector.is_invoice(d)                    # tani Claude yes/no; CV/ebook odpada PRZED ekstrakcja
   → [tylko faktury]
   → (wolajacy) start_document(graf, d)        # extract -> validate -> classify -> [sedzia] -> human_review
   → bramka HITL (bez auto-approve)
```

---

## 5. Testy

**Jednostkowe (deterministyczne, bez sieci):**
- `_build_query` z wstrzykniętym `today=date(2026,6,23)` → zawiera `after:2026/06/23 before:2026/06/24`.
- `GmailAdapter.fetch(..., today=...)` → fake `service` dostaje zapytanie z poprawnym `after:/before:` (rozszerzenie istniejących testów `test_gmail.py`).
- `ClaudeInvoiceDetector.is_invoice` z fake-LLM zwracającym `InvoiceCheck(is_invoice=True/False)` → zwraca odpowiedni bool; multimodalna wiadomość zawiera PDF (blok `file`).
- `StubInvoiceDetector(result=...)` → zwraca skonfigurowane.
- `fetch_invoice_documents` z fake source (mix faktur i nie-faktur) + stub/per-doc detector → zwraca tylko faktury (kolejność zachowana).
- Zgodność z portem: `isinstance(ClaudeInvoiceDetector(...), InvoiceDetector)`, `isinstance(StubInvoiceDetector(), InvoiceDetector)`.

**Live-gated (`tests/live/test_invoice_detector_live.py` NEW):** skip bez `ANTHROPIC_API_KEY`/fixtur; realny `ClaudeInvoiceDetector` na fakturze → `True`. (Opcjonalnie na nie-fakturze → `False`, jeśli jest fixtura.)

---

## 6. Pliki / podział na taski (subagent-driven)

- **Task 0:** gałąź `feat/gmail-daily-invoice-detect` (utworzona), baseline (157+4).
- **Task 1:** `GmailAdapter` dzienny filtr (`_build_query`/`fetch` + `today`) + testy (TDD, `test_gmail.py`).
- **Task 2:** port `InvoiceDetector` (`ports.py`) + `StubInvoiceDetector` (`stub_detector.py`) + testy.
- **Task 3:** `ClaudeInvoiceDetector` (`claude_detector.py`, `InvoiceCheck`) + testy (fake-LLM) + live-gated.
- **Task 4:** `fetch_invoice_documents` (`runner.py`) + testy (fake source + stub detector).
- **Task 5:** lint + pełny suite (zielona baza).
- **Finał:** review opus + merge `--no-ff` do `main`.

---

## 7. Ryzyka / decyzje

- **Dzień kalendarzowy vs `newer_than:1d`:** wybrano `after:/before:` (kalendarzowy dzień, zgodnie z wymaganiem „z 23 czerwca"); `newer_than` to ruchome 24h — odrzucone.
- **`today` wstrzykiwany:** determinizm testów; domyślnie `date.today()` (jutro auto-szuka 24.06). Nie przecieka do portu `EmailSource`.
- **Detektor jako osobny call:** +1 tani LLM-call na PDF, ale nie-faktury pomijają drogą ekstrakcję; czysta separacja (port + stub), graf nietknięty.
- **Reuse `_mime_and_block`:** detektor importuje helper z `claude_extractor` (DRY, bez refaktoru ekstraktora).
- **Strefa czasowa Gmaila:** `after:/before:` działają wg daty konta — akceptowalne dla MVP (faktury z „dziś").
- **Granica:** ścieżka upload (Streamlit) bez detekcji — user świadomie wybiera plik.
