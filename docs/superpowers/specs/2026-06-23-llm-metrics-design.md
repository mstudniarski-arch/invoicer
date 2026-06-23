# Invoicer — Design: metryki LLM (koszt + latencja)

**Data:** 2026-06-23
**Status:** zatwierdzony projekt (realizacja subagent-driven, 1 plan)
**Realizuje:** każde realne wywołanie Claude (ekstrakcja / detekcja / reasoning) jest mierzone — liczba tokenów, szacowany koszt USD i latencja — i zapisywane do kolektora oraz do logu (bez PII).

---

## 1. Problem / kontekst

Pipeline woła Claude w trzech miejscach (`ClaudeInvoiceExtractor`, `ClaudeInvoiceDetector`, `ClaudeExceptionReasoner`) — wszystkie przez `langchain_anthropic.ChatAnthropic(...).with_structured_output(Schema).invoke(...)`. Dziś nie ma żadnej widoczności kosztu ani czasu tych wywołań: nie wiadomo, ile tokenów zjada faktura, ile to kosztuje, ani które wywołanie jest wolne. Dla projektu portfolio (i dla realnego użycia) to brakujący element obserwowalności.

**Zweryfikowane empirycznie** (`/tmp/verify_metrics.py`): `BaseCallbackHandler` przekazany do `ChatAnthropic(callbacks=[...])` **dostaje `usage_metadata`** (input/output tokens) w `on_llm_end` — także na ścieżce `with_structured_output`, mimo że obiekt zwracany z `.invoke()` (Pydantic) nie niesie usage. To jest mechanizm, na którym opieramy metryki.

**Wzorce w repo do uszanowania:**
- Porty + wstrzykiwalne zależności (CI: fake `llm`; live: leniwy `ChatAnthropic`). Adaptery mają już param `llm` i `model`.
- Logowanie z redakcją PII (`security.py`: `redact_pii` + `RedactingFilter` na root handlerze). Metryki **nie zawierają PII** (nazwa modelu, liczby) — i tak przechodzą przez filtr bez zmian.
- Testy live-gated na `ANTHROPIC_API_KEY`; reszta deterministyczna na fake'ach.

---

## 2. Zakres

**W zakresie:**
- Nowy moduł `src/invoicer/observability.py`:
  - cennik `_PRICING` (model → stawki USD/1M tokenów) + `estimate_cost(model, input_tokens, output_tokens) -> float`.
  - `LlmCall` (dataclass: model, input_tokens, output_tokens, cost_usd, latency_ms).
  - `LlmMetrics` — kolektor wywołań (`.calls`, `.record(...)`, `.totals()`).
  - `LlmMetricsCallback(BaseCallbackHandler)` — mierzy latencję (per `run_id`), czyta `usage_metadata`, liczy koszt, zapisuje `LlmCall` do kolektora i loguje jedną linię do loggera `invoicer.metrics` (bez PII).
- Modyfikacja 3 adapterów Claude: dodanie opcjonalnego `callbacks: list | None = None` do `__init__` i przekazanie do `ChatAnthropic(model=..., callbacks=callbacks)`.
- Testy jednostkowe (cennik, callback na syntetycznych zdarzeniach, log bez PII) + jeden live-gated (realny call → wpis w kolektorze z tokenami/kosztem/latencją > 0).

**Poza zakresem (świadome YAGNI):**
- Eksport do Prometheus/OTel/StatsD, dashboardy, agregacja między procesami — MVP to kolektor in-memory + log.
- Trwałe składowanie metryk (baza, plik) — log linii wystarcza; persystencja to follow-up.
- Mierzenie cache'owanych tokenów (`cache_read_input_tokens`) i osobnej wyceny cache — `usage_metadata` z LangChain daje input/output; rozbicie cache to follow-up.
- Budżety/limity kosztów, alerty — tylko pomiar, bez egzekwowania.
- Zmiana modelu wołań — adaptery dalej domyślnie `claude-sonnet-4-6`.

---

## 3. Architektura

### 3.1 Cennik i wycena (`observability.py`)

```python
# Stawki USD za 1M tokenów. Źródło: referencja claude-api (cache 2026-06-04).
# Przybliżone i konfigurowalne — łatwo zaktualizować/rozszerzyć.
_PRICING: dict[str, tuple[float, float]] = {
    # model: (input_usd_per_mtok, output_usd_per_mtok)
    "claude-sonnet-4-6": (3.0, 15.0),   # model domyślny adapterów
    "claude-opus-4-8": (5.0, 25.0),
    "claude-haiku-4-5": (1.0, 5.0),
}


def estimate_cost(model: str, input_tokens: int, output_tokens: int) -> float:
    """Szacowany koszt USD. Nieznany model → 0.0 (nie wysadzamy pomiaru — tokeny/latencja i tak są zapisane)."""
    rates = _PRICING.get(model)
    if rates is None:
        return 0.0
    in_rate, out_rate = rates
    return input_tokens / 1_000_000 * in_rate + output_tokens / 1_000_000 * out_rate
```

Stawki są jawnie oznaczone jako **przybliżone** (cache referencji, nie live billing). To wystarcza do obserwowalności rzędu wielkości; nie jest to faktura od Anthropic.

### 3.2 Model danych i kolektor

```python
@dataclass(frozen=True)
class LlmCall:
    model: str
    input_tokens: int
    output_tokens: int
    cost_usd: float
    latency_ms: int


class LlmMetrics:
    """Kolektor in-memory wywołań LLM."""
    def __init__(self) -> None:
        self.calls: list[LlmCall] = []

    def record(self, call: LlmCall) -> None:
        self.calls.append(call)

    def totals(self) -> dict:
        # n_calls, input_tokens, output_tokens, total_tokens, cost_usd, latency_ms (suma)
        ...
```

`totals()` sumuje po `self.calls` (puste → same zera). Deterministyczne, łatwo testowalne.

### 3.3 Callback metryk

```python
class LlmMetricsCallback(BaseCallbackHandler):
    """Mierzy latencję (per run_id) i koszt każdego wywołania LLM; zapisuje do kolektora + loguje."""
    def __init__(self, metrics: LlmMetrics, *, model: str, clock=time.monotonic, logger=None) -> None:
        ...
    def on_chat_model_start(self, serialized, messages, *, run_id, **kw) -> None: ...  # start[run_id] = clock()
    def on_llm_start(self, serialized, prompts, *, run_id, **kw) -> None: ...           # j.w. (obie ścieżki)
    def on_llm_end(self, response, *, run_id, **kw) -> None:
        # latency_ms z clock(); usage_metadata z response.generations[0][0].message;
        # cost = estimate_cost(self._model, in, out); metrics.record(LlmCall(...));
        # logger.info("llm_call model=... input_tokens=... output_tokens=... cost_usd=... latency_ms=...")
```

- **Model** podawany przy konstrukcji (adapter zna swój `self._model`) — autorytatywne źródło nazwy; tokeny z `usage_metadata`. Brak `usage_metadata` (None) → tokeny 0, koszt 0, ale latencja zapisana (degradacja łagodna).
- **`clock` wstrzykiwalny** (domyślnie `time.monotonic`) — test ustawia deterministyczny zegar.
- **Log** to jedna linia w loggerze `invoicer.metrics`, wyłącznie: model + liczby. Zero PII — przechodzi przez `RedactingFilter` bez zmian, ale i tak nie ma czego redagować.

### 3.4 Wpięcie w adaptery

Każdy z `claude_extractor.py` / `claude_detector.py` / `claude_reasoner.py`:

```python
def __init__(self, *, model: str = _DEFAULT_MODEL, llm: Any = None,
             callbacks: list | None = None) -> None:
    self._model = model
    self._llm = llm
    self._callbacks = callbacks

def _client(self):
    if self._llm is None:
        from langchain_anthropic import ChatAnthropic
        self._llm = ChatAnthropic(model=self._model, callbacks=self._callbacks)
    return self._llm
```

- Przy wstrzykniętym `llm` (testy/CI) `callbacks` jest pomijany — fake nie odpala callbacków; metryki dotyczą realnych wywołań. Istniejące testy fake-llm **bez zmian**.
- Brak `callbacks` (None) → zachowanie identyczne jak dziś.

### 3.5 Zależności

**Brak nowych.** `BaseCallbackHandler` pochodzi z `langchain_core` (już obecny), `ChatAnthropic` z `langchain_anthropic` (już obecny). Tylko nowy moduł + dopisanie kwargs.

---

## 4. Przepływ danych

```
metrics = LlmMetrics()
cb = LlmMetricsCallback(metrics, model="claude-sonnet-4-6")
extractor = ClaudeInvoiceExtractor(callbacks=[cb])
    → _client() → ChatAnthropic(model=..., callbacks=[cb])
    → .with_structured_output(Invoice).invoke([msg])
        on_chat_model_start(run_id)          # start zegara
        ... realny call do Claude ...
        on_llm_end(response z usage_metadata) # latency + tokeny → estimate_cost → metrics.record + log
metrics.totals() → {n_calls, input_tokens, output_tokens, cost_usd, latency_ms}
log "invoicer.metrics": llm_call model=claude-sonnet-4-6 input_tokens=654 output_tokens=35 cost_usd=0.002... latency_ms=1473
```

---

## 5. Testy

- `estimate_cost`: znany model (np. sonnet 1000 in + 1000 out = 0.003 + 0.015 = 0.018 USD); nieznany model → 0.0.
- `LlmMetricsCallback` na syntetycznych zdarzeniach: `on_chat_model_start(run_id=X)` (deterministyczny `clock`) → `on_llm_end(LLMResult z message.usage_metadata input=654, output=35)` → `metrics.calls` ma 1 `LlmCall` (input=654, output=35, cost_usd>0, latency_ms zgodny z zegarem); `totals()` agreguje (np. 2 wywołania → sumy).
- `usage_metadata is None` → `LlmCall` z tokenami 0, koszt 0, latencja zapisana (brak wyjątku).
- Log bez PII: `caplog` na `invoicer.metrics` → linia zawiera `model=`, `input_tokens=`, `cost_usd=`, `latency_ms=`; brak jakiegokolwiek PII (nie ma czego — asercja na zawartość pól).
- Adapter przekazuje `callbacks`: `ClaudeInvoiceExtractor(callbacks=[cb])` z zamockowanym `ChatAnthropic` → `_client()` woła `ChatAnthropic(model=..., callbacks=[cb])` (patch konstruktora). Istniejące testy fake-llm dalej zielone.
- **Live-gated** (`ANTHROPIC_API_KEY`): realny `ClaudeInvoiceDetector(callbacks=[cb])` (lub extractor) na małym dokumencie → po `invoke` `metrics.calls` niepuste, `input_tokens>0`, `output_tokens>0`, `cost_usd>0`, `latency_ms>0`. Skip bez klucza.

---

## 6. Ryzyka / decyzje

- **Stawki przybliżone, nie billing.** `_PRICING` to cache referencji (2026-06-04), oznaczony komentarzem; służy do obserwowalności rzędu wielkości, nie do rozliczeń. Aktualizacja = edycja dicta.
- **Nieznany model → koszt 0.0, ale tokeny/latencja zapisane.** Łagodna degradacja — pomiar nie pada, gdy ktoś zmieni model bez aktualizacji cennika.
- **Model z konstrukcji callbacku, nie z odpowiedzi.** Adapter zna swój model — autorytatywne i testowalne; tokeny z `usage_metadata`.
- **`with_structured_output` gubi usage w zwracanym obiekcie** — dlatego mierzymy callbackiem (`on_llm_end`), nie po zwrotce `.invoke()`. Zweryfikowane empirycznie.
- **Brak PII w metrykach** — log jest bezpieczny; przechodzi przez istniejący `RedactingFilter` bez modyfikacji.
- **Granice:** graf / Streamlit / orkiestracja nietknięte; metryki to opt-in przez `callbacks=[...]` na adapterze. Bez przekazania callbacku zachowanie identyczne jak dziś.
- **YAGNI:** brak eksportu do systemów monitoringu, persystencji, budżetów — kolektor in-memory + log; rozszerzenia to follow-up.
