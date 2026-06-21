# Invoicer — Design: redakcja PII w ścieżce logów (§9)

**Data:** 2026-06-21
**Status:** zatwierdzony projekt (do realizacji subagent-driven, jak Plany 01–08)
**Realizuje:** spec §9 — „kroki rozumujące / logi nie powinny wyciekać PII". Domyka follow-up z finałowego review Planu 08.

---

## 1. Problem

Plan 08 dodał `src/invoicer/security.py::redact_pii(text)` (maskuje NIP / konto / e-mail), ale **narzędzie ma zero callerów** — nie jest wpięte w żadną ścieżkę logowania, więc obecnie nie chroni niczego. Finałowy review P08 wskazał konkretny przeciek: `MockSubiektSink.post` (`src/invoicer/adapters/mock_subiekt.py:20`) loguje `seller.name` i `total_gross` surowo na poziomie INFO.

**Nowy kontekst:** użytkownik planuje użyć **realnego Subiekta do testów** → realne PII w logach, oraz przyszły adapter `SubiektSferaSink` obok `MockSubiektSink`. Redakcja musi być **adapter-agnostyczna** i nie do obejścia/zapomnienia przy dodaniu nowego sinka.

### Ustalenia z eksploracji kodu
- **Jedyne** miejsce w `src/` logujące przez stdlib logging to `MockSubiektSink.post`. Loguje przez **`record.args`** (leniwe `%s`), nie w gotowym `record.msg` — więc filtr operujący na `record.msg` by tego NIE złapał; trzeba renderować przez `record.getMessage()`.
- **Brak centralnej konfiguracji logowania** w repo (żadnego `basicConfig`/handlera/filtra) — biblioteka poprawnie zostawia to aplikacji.
- Streamlit używa `st.warning/info/error` (UI display, nie stdlib logging; pokazuje wyłącznie nie-PII flagi/summary) — **poza zakresem** redakcji.

---

## 2. Zakres

**W zakresie:**
- Rozszerzenie `redact_pii` o realne formaty PL: IBAN z prefiksem `PL`, NIP z separatorami, konto w grupach spacji.
- `RedactingFilter(logging.Filter)` — redaguje `record.getMessage()` (łapie PII z `record.args`).
- `install_redaction()` — idempotentny helper podpinający filtr na poziomie **root-handlera** (uniwersalnie, dla każdego adaptera i nawet third-party).
- Wpięcie `install_redaction()` w `streamlit_app.py` (przy starcie).
- Testy charakteryzacyjne: formaty, filtr (PII w args), idempotencja, child-logger, integracja z realnym `MockSubiektSink`, brak false-positive na datach.

**Poza zakresem (świadome YAGNI / osobne plany):**
- **`SubiektSferaSink` (Windows/COM) + jego live-gated test** — osobny przyszły plan; redakcja i tak go automatycznie pokryje (root-level filtr). Brak Windows/COM w tym środowisku.
- Strukturalne logowanie (JSON), korelacja, eksport do SIEM — niepotrzebne w MVP.
- Redakcja w warstwie UI Streamlit (`st.*`) — to nie logi, pokazuje nie-PII.

---

## 3. Architektura

Wszystko w jednym spójnym module `src/invoicer/security.py` (rozszerzenie istniejącego).

### 3.1 `redact_pii(text: str) -> str` (rozszerzony, pure function)
Kolejność podstawień: **od najdłuższych/najspecyficzniejszych do najkrótszych**, by uniknąć częściowych trafień. Over-masking jest bezpieczny (per review P08); przeciek nie — przy wątpliwości maskujemy.

Maskowane wzorce (tokeny: `[KONTO]`, `[NIP]`, `[EMAIL]`):
1. **PL IBAN** — `PL` + 26 cyfr, z opcjonalnymi spacjami w grupach (np. `PL61 1090 1014 0000 0712 1981 2874`) → `[KONTO]`.
2. **Konto 26 cyfr** — ciągłe (istniejące) oraz w grupach spacji (`NN NNNN NNNN …`) → `[KONTO]`.
3. **NIP z separatorami** — konkretne grupowania PL: `\d{3}-\d{3}-\d{2}-\d{2}` oraz `\d{3}-\d{2}-\d{2}-\d{3}` → `[NIP]`. (Specyficzne grupowania, NIE generyczne „cyfry+separatory" — by nie łapać dat ISO `2026-06-01`.)
4. **NIP 10 cyfr** — ciągłe (istniejące) → `[NIP]`.
5. **E-mail** — (istniejące) → `[EMAIL]`.

**Idempotencja:** `redact_pii(redact_pii(x)) == redact_pii(x)` — tokeny `[NIP]` itp. nie wpadają w żaden wzorzec. Wymóg konieczny dla bezpieczeństwa przy wielu handlerach.

**Anti-false-positive:** daty ISO (`2026-06-01`), godziny (`10:00:00`), krótkie liczby — NIE redagowane. Świadomie NIE pokrywamy PESEL/REGON/telefonów w tym iteracji (YAGNI; nie pojawiają się w obecnych logach faktur).

### 3.2 `RedactingFilter(logging.Filter)`
```
def filter(self, record):
    record.msg = redact_pii(record.getMessage())
    record.args = ()
    return True
```
- `getMessage()` renderuje `%s`-argumenty **przed** redakcją → łapie PII z `record.args` (jak w `MockSubiektSink`).
- Po redakcji czyści `args`, by handlery nie re-renderowały surowych wartości.
- Bezstanowy, reużywalny. Zawsze zwraca `True` (filtr transformujący, nie odrzucający).

### 3.3 `install_redaction(logger: logging.Logger | None = None) -> None`
- Domyślnie cel = **root logger** (`logging.getLogger()`).
- Jeśli cel nie ma handlerów → dodaje `StreamHandler` (żeby było co filtrować/emitować).
- Podpina `RedactingFilter` do **każdego handlera** celu (filtr na handlerze łapie też rekordy z child-loggerów `invoicer.*` propagujące w górę oraz z przyszłego `SubiektSferaSink`).
- **Idempotentny** — nie dubluje filtra/handlera przy powtórnym wywołaniu (wykrywa istniejący `RedactingFilter`).
- Redaguje **wszystkie** rekordy docierające do handlera (over-masking third-party logów jest bezpieczny i pożądany — np. realny Subiekt/COM lub httpx mogłyby logować PII).
- Dokumentacja: wołać **po** konfiguracji logowania aplikacji (Streamlit startup; harness testowy / uruchomienie z realnym Subiektem).

### 3.4 Wpięcie
- `streamlit_app.py`: `install_redaction()` w bloku startowym (raz, obok inicjalizacji `session_state`).
- `MockSubiektSink` i przyszły `SubiektSferaSink` — **bez zmian**; pokrywa je filtr (DRY, nie da się zapomnieć).

---

## 4. Przepływ danych

```
logger.info("...sprzedawca=%s brutto=%s", seller.name, total_gross)
        │  (PII w record.args)
        ▼
record propaguje do root handlera  ──►  RedactingFilter.filter(record)
                                              │  record.getMessage() renderuje args
                                              │  redact_pii(...) maskuje
                                              ▼
                                    handler.emit(zredagowana linia)  ──►  brak PII w logu
```
Każdy adapter (`MockSubiektSink`, przyszły `SubiektSferaSink`) i każdy log `invoicer.*` pokryty automatycznie.

---

## 5. Testy (TDD, charakteryzacyjne)

`tests/unit/test_security.py` (rozszerzenie istniejących 5 testów):
- Nowe formaty maskowane: `PL61 1090 1014 0000 0712 1981 2874` → `[KONTO]`; `526-000-12-46` → `[NIP]`; `61 1090 1014 0000 0712 1981 2874` → `[KONTO]`.
- **Brak false-positive:** `"2026-06-01 10:00:00 faktura"` → bez zmian; `"VAT 23%"` → bez zmian.
- Idempotencja: `redact_pii(redact_pii(s)) == redact_pii(s)`.
- Istniejące 5 testów (NIP/konto/email/passthrough/multi-PII) — zielone.

`tests/unit/test_logging_redaction.py` (NEW):
- `RedactingFilter`: rekord z PII w `record.args` → po `filter()` `record.getMessage()` bez NIP/konta/emaila, z tokenami.
- `install_redaction` idempotencja: dwukrotne wywołanie → jeden `RedactingFilter` na handlerze.
- **Child-logger:** log przez `logging.getLogger("invoicer.mock_subiekt")` po `install_redaction()` → przechwycony output zredagowany (dowód, że filtr na root-handlerze łapie child-loggery).
- **Integracja:** realny `MockSubiektSink().post(payload)` (z `seller.name` zawierającym e-mail/NIP-podobne) pod zainstalowaną redakcją → przechwycony log bez PII.

Testy używają własnego capturing handlera z podpiętym `RedactingFilter` (deterministyczne, bez zależności od globalnego stanu logowania; sprzątają handlery w teardown).

---

## 6. Podział na taski (subagent-driven)

- **Task 0:** gałąź `feat/redact-pii-logging` (utworzona), baseline (`uv run pytest -q` → 133+3).
- **Task 1:** rozszerzenie `redact_pii` (regexy PL IBAN / separowany NIP / grupowane konto) + testy formatów i idempotencji (TDD).
- **Task 2:** `RedactingFilter` + `install_redaction` + `tests/unit/test_logging_redaction.py` (filtr, idempotencja, child-logger, integracja MockSubiektSink) (TDD).
- **Task 3:** wpięcie `install_redaction()` w `streamlit_app.py` + sanity (parse/ruff; UI nietestowane jednostkowo).
- **Task 4:** lint + pełny suite (zielona baza), porządkowy commit jeśli trzeba.
- **Finał:** review opus całości + merge `--no-ff` do `main`.

---

## 7. Ryzyka / decyzje

- **Root-level vs `invoicer`-scoped vs `propagate=False`:** wybrano root-level filtr — najmocniejsza, adapter-agnostyczna gwarancja przy nadchodzącym realnym Subiekcie (realne PII). Odrzucono `propagate=False` (utrudniałby oglądanie zredagowanych logów w normalnej konfiguracji aplikacji). Odrzucono jawną redakcję per call-site (łatwo zapomnieć przy nowym adapterze — to luka, którą review zgłosił).
- **Mutacja `record` w filtrze** to znany wzorzec redakcji; bezpieczna dzięki idempotencji `redact_pii` (wiele handlerów / wielokrotne filtrowanie nie psuje wyniku).
- **Timing instalacji:** `install_redaction()` wołać po konfiguracji logowania; handlery dodane później nie dostaną filtra — udokumentowane.
- **Separowane NIP-y:** ryzyko nietrafienia nietypowych formatów (akceptowalne — over-masking bezpieczny, pełne pokrycie to YAGNI).
