# Invoicer — Design: trigger zdarzeniowy (polling Gmaila + dedup)

**Data:** 2026-06-25
**Status:** zatwierdzony projekt (do realizacji subagent-driven / TDD)
**Realizuje:** zamianę triggera zaciągu z **sztywnej godziny dziennej** (cron 08:00) na **reaktywny polling** — flow rusza, gdy tylko przyjdzie nowy mail z załącznikiem `.pdf`. Wymaga trwałej deduplikacji (polling ≠ jednorazowy przebieg dzienny).

---

## 1. Problem / kontekst

Dziś trigger to `CronTrigger(hour, minute)` w `build_scheduler` ([scheduler.py:56](../../../src/invoicer/scheduler.py)) — `AsyncIOScheduler` odpala `run_daily_intake` raz dziennie (domyślnie 08:00 `Europe/Warsaw`). `GmailAdapter.fetch` pyta o PDF-y „od nadawcy z bieżącego dnia kalendarzowego" (`after:dziś before:jutro`, [gmail.py:12](../../../src/invoicer/adapters/gmail.py)).

**Wymaganie użytkownika:** nie czekać do ustalonej godziny — *jeżeli tylko przyjdzie nowy mail z plikiem `.pdf`, flow się uruchamia*.

**Decyzja (z brainstormu):**
- **Mechanizm = polling** (APScheduler `IntervalTrigger`), nie Gmail push (watch + Pub/Sub). Push daje reakcję w sekundy, ale wymaga GCP Pub/Sub topic, śledzenia `historyId` i odnawiania `watch` co 7 dni — odrzucone jako nieproporcjonalna złożoność dla projektu.
- **Interwał = 5 min** (konfigurowalny przez env `INTAKE_INTERVAL_MINUTES`).
- **Polityka błędów = at-most-once:** każdy załącznik próbujemy **dokładnie raz**; przy błędzie zapisujemy go jako obsłużony (brak ponawiania), wysyłamy **alert „wymaga manualnej interwencji"**. Świadoma decyzja: przejściowy błąd nie jest auto-ponawiany (operator interweniuje ręcznie, np. `scripts/run_flow_now.py`), w zamian brak zalewania alertami i brak duplikatów próśb na WhatsApp.

**Wzorce w repo:** `build_scheduler` (APScheduler, `max_instances=1`, `coalesce`). `PendingApprovals` ([approvals.py](../../../src/invoicer/approvals.py)) — trwały rejestr w istniejącym SQLite (wzorzec dla store'a dedup). `run_daily_intake` — per-faktura `try/except` + wstrzykiwane `counters`/`alert`/`request_fn`.

---

## 2. Zakres

**W zakresie:**
- `build_scheduler` — `CronTrigger` → `IntervalTrigger(minutes=interval)`; `id` `daily-intake` → `intake`.
- Nowy store `ProcessedDocuments` (SQLite) — trwała deduplikacja po kluczu dokumentu.
- `run_daily_intake` → `run_intake`: pomija znane klucze; zapisuje `done`/`failed`; alert przy błędzie.
- `InvoiceDocument` + `GmailAdapter` — przeniesienie `message_id` (do klucza dedup).
- Okno zapytania Gmaila: `after:/before:` (dzień kalendarzowy) → `newer_than:2d` (bezpieczne dzięki dedup; nie gubi maili „zza północy").
- `AppSettings`/env: usunięcie `INTAKE_HOUR`/`INTAKE_MINUTE`/`INTAKE_TZ`, dodanie `INTAKE_INTERVAL_MINUTES`; wstrzyknięcie store'a w `_job`.
- Aktualizacja `.env.example`, `fly.toml`/docs odwołujących się do `INTAKE_HOUR`.
- Testy (TDD): `test_scheduler.py`, `test_gmail.py`, nowy `test_processed.py`.

**Poza zakresem (świadome YAGNI):**
- Gmail push / Pub/Sub / `users().watch()` — odrzucone (patrz §1).
- Ponawianie błędów, backoff, licznik prób — odrzucone (polityka at-most-once).
- Oznaczanie maili jako przeczytane / labelki — wymaga OAuth poza `gmail.readonly`; dedup robimy po stronie aplikacji.
- Graf LangGraph, bramka HITL, webhook, Streamlit-upload — **nietknięte**.
- Detekcja faktury (`InvoiceDetector`) — bez zmian; dedup działa na warstwie zaciągu.

---

## 3. Architektura

### 3.1 `build_scheduler` — interwał zamiast crona (`scheduler.py` MOD)
```python
def build_scheduler(job, *, interval_minutes: int) -> AsyncIOScheduler:
    sched = AsyncIOScheduler()
    sched.add_job(
        job, IntervalTrigger(minutes=interval_minutes),
        id="intake", max_instances=1, coalesce=True, misfire_grace_time=60,
    )
    return sched
```
`max_instances=1` + `coalesce=True`: jeśli przebieg przeciągnie się ponad interwał (dużo faktur), kolejny tick jest **pominięty**, nie nakłada się równolegle. `tz` zbędne (interwał jest względny).

### 3.2 `ProcessedDocuments` — trwała deduplikacja (`src/invoicer/processed.py` NEW)
Wzorzec jak `PendingApprovals` (ten sam plik SQLite, `check_same_thread=False`).
```python
class ProcessedDocuments:
    """Trwaly zbior obsluzonych dokumentow (idempotencja pollingu).
    Klucz = message_id:filename. Status done|failed — oba => 'juz obsluzony, pomijaj'."""
    def __init__(self, db_path: str) -> None: ...  # CREATE TABLE IF NOT EXISTS processed_documents(
                                                   #   doc_key TEXT PRIMARY KEY, status TEXT NOT NULL, updated_at TEXT)
    def seen(self, doc_key: str) -> bool: ...      # SELECT 1 ... LIMIT 1  (dowolny status)
    def mark(self, doc_key: str, status: str) -> None: ...  # INSERT OR REPLACE, status in {"done","failed"}
```
Klucz dokumentu liczony z `InvoiceDocument`: `f"{message_id}:{filename}"`; gdy `message_id is None` (upload/fixtura) → `f"sha256(content):{filename}"`. Helper `document_key(doc) -> str` w `processed.py` (obok store'a; importowany przez `scheduler.py` i testy).

### 3.3 `InvoiceDocument` + `GmailAdapter` — przeniesienie `message_id` (`models.py`, `gmail.py` MOD)
- `InvoiceDocument`: dodać `message_id: str | None = None` (kompatybilne wstecz; upload/fixtura zostają `None`).
- `GmailAdapter.fetch`: przy tworzeniu `InvoiceDocument` ustawić `message_id=ref["id"]`.

### 3.4 Okno zapytania Gmaila (`gmail.py` MOD)
`_build_query(sender, *, today)` → `_build_query(sender, *, lookback_days: int = 2)`:
`from:{token} newer_than:{lookback_days}d has:attachment filename:pdf`. Ruchome 48h zamiast „kalendarzowego dziś" — mail z 23:59 odpytany o 00:01 nie wypada z okna. Dzięki trwałej dedup szersze okno jest bezpieczne (nic nie przetworzy się dwa razy).

### 3.5 `run_intake` (dawniej `run_daily_intake`) (`scheduler.py` MOD)
```python
def run_intake(graph, channel, registry, source, detector, *, sender, phone,
               counters, processed: ProcessedDocuments,
               request_fn=request_invoice_approval, alert=lambda *_: None) -> None:
    docs = fetch_invoice_documents(source, detector, sender)   # blad fetch -> alert + raise (jak dzis)
    for doc in docs:
        key = document_key(doc)
        if processed.seen(key):
            continue
        thread_id = f"intake-{uuid.uuid4()}"
        try:
            request_fn(graph, channel, registry, doc, thread_id=thread_id, phone=phone)
            processed.mark(key, "done"); counters.incr_processed()
        except Exception as exc:                               # at-most-once: zapisz failed, NIE ponawiaj
            processed.mark(key, "failed"); counters.incr_failed()
            alert(doc.filename, f"wymaga manualnej interwencji: {exc}")
```
Różnica vs dziś: pre-check `processed.seen` + `processed.mark` na obu ścieżkach. Per-faktura `try/except` i alert na błędzie zaciągu — zachowane.

### 3.6 `app.py` — konfiguracja i wstrzyknięcie (`app.py` MOD)
- `AppSettings`: usuń `intake_hour/minute/tz`, dodaj `intake_interval_minutes: int = 5`.
- `_settings_from_env`: `INTAKE_INTERVAL_MINUTES` (domyślnie 5); usuń odczyt `INTAKE_HOUR/MINUTE/TZ`.
- `_lifespan`: `processed = ProcessedDocuments(db_path)`; `_job` woła `run_intake(..., processed=processed)`; `build_scheduler(_job, interval_minutes=settings.intake_interval_minutes)`.

---

## 4. Przepływ danych

```
IntervalTrigger co 5 min  ->  _job
   GmailAdapter.fetch(sender)               # from:.. newer_than:2d has:attachment filename:pdf  (+ message_id)
   -> detector.is_invoice(d)                # bez zmian
   -> for doc:
        key = message_id:filename
        processed.seen(key)?  -- TAK -->  pomin (idempotencja: nie spamuj WhatsApp)
                              -- NIE -->  request_invoice_approval -> bramka HITL
                                          sukces  -> processed.mark done
                                          blad    -> processed.mark failed + ALERT "manualna interwencja"
```

---

## 5. Testy (TDD)

**`test_processed.py` (NEW):**
- `seen` zwraca `False` dla nieznanego klucza, `True` po `mark(key, "done")` i po `mark(key, "failed")`.
- `mark` idempotentny (drugi `mark` tego samego klucza nie wywala — `INSERT OR REPLACE`).
- Trwałość: nowy `ProcessedDocuments(ten sam db_path)` widzi wcześniej zapisany klucz.
- `document_key`: różne `filename` w jednym `message_id` → różne klucze; `message_id=None` → klucz z hasha treści.

**`test_scheduler.py` (MOD):**
- `test_build_scheduler` — `IntervalTrigger` (`interval == timedelta(minutes=5)`), `id == "intake"`, `max_instances == 1`, `coalesce`. (Zastępuje asercje cron hour/minute.)
- `run_intake` pomija dokument, którego klucz jest już w (fake) `processed` — `request_fn` nie wołany dla niego.
- `run_intake` zapisuje `done` po sukcesie, `failed` + **jeden alert** po błędzie; jedna zła faktura nie blokuje pozostałych (zachowane istniejące przypadki, z dołożonym `processed`).
- Błąd `fetch` → alert `("intake", ...)` + `raise` (zachowane).

**`test_gmail.py` (MOD):**
- `_build_query` → zawiera `newer_than:2d` (zamiast `after:/before:`).
- `GmailAdapter.fetch` ustawia `message_id` z `ref["id"]` na zwróconych `InvoiceDocument`.

---

## 6. Pliki / podział na taski (subagent-driven / TDD)

- **Task 0:** gałąź `feat/event-driven-intake`, baseline (suite zielony).
- **Task 1:** `ProcessedDocuments` + `document_key` (`processed.py`) + `test_processed.py`.
- **Task 2:** `InvoiceDocument.message_id` + `GmailAdapter` (`message_id`, `newer_than:2d`) + `test_gmail.py`.
- **Task 3:** `build_scheduler` (interwał) + `run_intake` (dedup + alert) + `test_scheduler.py`.
- **Task 4:** `app.py` (settings/env/lifespan) + `.env.example` + odwołania w `fly.toml`/docs/`scripts`.
- **Task 5:** lint (`ruff`) + pełny suite (zielona baza).
- **Finał:** review + merge `--no-ff`.

---

## 7. Ryzyka / decyzje

- **`newer_than:2d` zastępuje `after:/before:` z 2026-06-23** ([gmail-daily-invoice-detect](2026-06-23-gmail-daily-invoice-detect-design.md)): tamta decyzja (dzień kalendarzowy) była słuszna dla **crona o 08:00**; przy pollingu przez północ „kalendarzowe dziś" gubi maile późnowieczorne. Trwała dedup eliminuje ryzyko podwójnego przetwarzania, więc ruchome okno jest teraz właściwe.
- **Polityka at-most-once (bez ponawiania):** wybór użytkownika — przejściowy błąd = pominięta faktura + alert „manualna interwencja", w zamian zero spamu alertami i zero duplikatów próśb WhatsApp. Operator ponawia ręcznie.
- **Idempotencja przed `request_approval`:** `processed.seen` sprawdzane **przed** wejściem w graf/Twilio → ten sam mail nie generuje powtórnych próśb co 5 min. Kluczowe dla modelu pollingowego.
- **Klucz `message_id:filename` (nie sam `message_id`):** jeden mail z 2 PDF = 2 faktury; dedup per załącznik. Fallback hash treści dla źródeł bez ID (upload/fixtura).
- **`max_instances=1` + `coalesce`:** przebieg dłuższy niż interwał nie nakłada się; wolniejszy zaciąg po prostu pomija tick.
- **Współdzielony plik SQLite:** `ProcessedDocuments` to kolejne połączenie do istniejącego pliku (WAL już aktywne — `.sqlite-wal/.shm`), jak `PendingApprovals`/checkpointer.
- **Limity Gmail API:** polling co 5 min × `messages.list/get` — znikome wobec limitów; brak ryzyka.
