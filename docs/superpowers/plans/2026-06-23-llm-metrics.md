# LLM Metrics (koszt + latencja) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Mierzyć każde realne wywołanie Claude (tokeny, szacowany koszt USD, latencja) przez callback LangChain i zapisywać do kolektora in-memory oraz logu bez PII.

**Architecture:** Nowy moduł `src/invoicer/observability.py` z cennikiem (`estimate_cost`), modelem danych (`LlmCall`), kolektorem (`LlmMetrics`) i `LlmMetricsCallback(BaseCallbackHandler)` mierzącym latencję per `run_id` i czytającym `usage_metadata` w `on_llm_end`. Trzy adaptery Claude (`ClaudeVisionExtractor`, `ClaudeInvoiceDetector`, `ClaudeExceptionReasoner`) dostają opcjonalny `callbacks=`, przekazywany do `ChatAnthropic(...)`. Metryki są opt-in: bez przekazania callbacku zachowanie identyczne jak dziś.

**Tech Stack:** Python 3, `uv`, `pytest`, `ruff`; `langchain-core` (`BaseCallbackHandler`, `LLMResult`/`ChatGeneration`/`AIMessage`) i `langchain-anthropic` (`ChatAnthropic`) — wszystkie już w zależnościach. Brak nowych deps.

**Spec:** `docs/superpowers/specs/2026-06-23-llm-metrics-design.md`

**Branch:** `feat/llm-metrics` (już utworzona z `main` @ `bfe84ac`; spec scommitowany jako `9d2e3bd`). Baseline: 186 passed / 6 skipped, ruff czysty.

---

## File Structure

| Plik | Odpowiedzialność | Akcja |
|------|------------------|-------|
| `src/invoicer/observability.py` | Cennik, `estimate_cost`, `LlmCall`, `LlmMetrics`, `LlmMetricsCallback`, helper `_usage_from_response` | Create (Task 1→3) |
| `src/invoicer/adapters/claude_extractor.py` | + param `callbacks` → `ChatAnthropic` | Modify (Task 4) |
| `src/invoicer/adapters/claude_detector.py` | + param `callbacks` → `ChatAnthropic` | Modify (Task 4) |
| `src/invoicer/adapters/claude_reasoner.py` | + param `callbacks` → `ChatAnthropic` | Modify (Task 4) |
| `tests/unit/test_observability.py` | Cennik + kolektor + callback (syntetyczne zdarzenia, log bez PII) | Create (Task 1→3) |
| `tests/unit/test_metrics_wiring.py` | Adaptery przekazują `callbacks` do `ChatAnthropic` | Create (Task 4) |
| `tests/live/test_metrics_live.py` | Realny call → wpis w kolektorze (gated) | Create (Task 5) |

Wszystkie komendy uruchamiać z katalogu repo `/Users/mski/Developer/Invoicer`. `pytest` ma `pythonpath=["src"]`, więc `from invoicer...` działa.

---

### Task 1: Cennik + `estimate_cost`

**Files:**
- Create: `src/invoicer/observability.py`
- Test: `tests/unit/test_observability.py`

- [ ] **Step 1: Write the failing tests**

Utwórz `tests/unit/test_observability.py`:

```python
import pytest

from invoicer.observability import estimate_cost


def test_estimate_cost_sonnet():
    # 1000 in + 1000 out @ (3, 15) USD/Mtok = 0.003 + 0.015
    assert estimate_cost("claude-sonnet-4-6", 1000, 1000) == pytest.approx(0.018)


def test_estimate_cost_opus():
    # 1000 in + 1000 out @ (5, 25) USD/Mtok = 0.005 + 0.025
    assert estimate_cost("claude-opus-4-8", 1000, 1000) == pytest.approx(0.030)


def test_estimate_cost_unknown_model_is_zero():
    assert estimate_cost("model-ktorego-nie-ma", 1000, 1000) == 0.0


def test_estimate_cost_zero_tokens():
    assert estimate_cost("claude-sonnet-4-6", 0, 0) == 0.0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/test_observability.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'invoicer.observability'`

- [ ] **Step 3: Create the module with pricing**

Utwórz `src/invoicer/observability.py`:

```python
from __future__ import annotations

# Stawki USD za 1M tokenow. Zrodlo: referencja claude-api (cache 2026-06-04).
# Przyblizone i konfigurowalne — latwo zaktualizowac/rozszerzyc. To nie jest billing Anthropic.
_PRICING: dict[str, tuple[float, float]] = {
    # model: (input_usd_per_mtok, output_usd_per_mtok)
    "claude-sonnet-4-6": (3.0, 15.0),  # model domyslny adapterow
    "claude-opus-4-8": (5.0, 25.0),
    "claude-haiku-4-5": (1.0, 5.0),
}


def estimate_cost(model: str, input_tokens: int, output_tokens: int) -> float:
    """Szacowany koszt USD wywolania. Nieznany model -> 0.0 (tokeny/latencja i tak sa zapisane)."""
    rates = _PRICING.get(model)
    if rates is None:
        return 0.0
    in_rate, out_rate = rates
    return input_tokens / 1_000_000 * in_rate + output_tokens / 1_000_000 * out_rate
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_observability.py -v`
Expected: PASS (4 passed)

- [ ] **Step 5: Commit**

```bash
git add src/invoicer/observability.py tests/unit/test_observability.py
git commit -m "feat(observability): pricing table + estimate_cost"
```

---

### Task 2: `LlmCall` + kolektor `LlmMetrics`

**Files:**
- Modify: `src/invoicer/observability.py`
- Test: `tests/unit/test_observability.py`

- [ ] **Step 1: Write the failing tests**

Dopisz do `tests/unit/test_observability.py` (na końcu pliku):

```python
from invoicer.observability import LlmCall, LlmMetrics


def test_metrics_record_and_totals():
    m = LlmMetrics()
    m.record(LlmCall("claude-sonnet-4-6", 100, 20, 0.0006, 500))
    m.record(LlmCall("claude-sonnet-4-6", 200, 30, 0.0011, 700))
    t = m.totals()
    assert t["n_calls"] == 2
    assert t["input_tokens"] == 300
    assert t["output_tokens"] == 50
    assert t["total_tokens"] == 350
    assert t["latency_ms"] == 1200
    assert t["cost_usd"] == pytest.approx(0.0017)


def test_metrics_empty_totals():
    t = LlmMetrics().totals()
    assert t["n_calls"] == 0
    assert t["input_tokens"] == 0
    assert t["output_tokens"] == 0
    assert t["total_tokens"] == 0
    assert t["cost_usd"] == 0
    assert t["latency_ms"] == 0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/test_observability.py -v`
Expected: FAIL — `ImportError: cannot import name 'LlmCall'`

- [ ] **Step 3: Add the dataclass and collector**

W `src/invoicer/observability.py` zmień pierwszą linię importów i dopisz klasy **po** funkcji `estimate_cost`.

Na górze pliku, pod `from __future__ import annotations`, dodaj import:

```python
from dataclasses import dataclass
```

Na końcu pliku dodaj:

```python
@dataclass(frozen=True)
class LlmCall:
    model: str
    input_tokens: int
    output_tokens: int
    cost_usd: float
    latency_ms: int


class LlmMetrics:
    """Kolektor in-memory wywolan LLM."""

    def __init__(self) -> None:
        self.calls: list[LlmCall] = []

    def record(self, call: LlmCall) -> None:
        self.calls.append(call)

    def totals(self) -> dict:
        input_tokens = sum(c.input_tokens for c in self.calls)
        output_tokens = sum(c.output_tokens for c in self.calls)
        return {
            "n_calls": len(self.calls),
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "total_tokens": input_tokens + output_tokens,
            "cost_usd": sum(c.cost_usd for c in self.calls),
            "latency_ms": sum(c.latency_ms for c in self.calls),
        }
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_observability.py -v`
Expected: PASS (6 passed)

- [ ] **Step 5: Commit**

```bash
git add src/invoicer/observability.py tests/unit/test_observability.py
git commit -m "feat(observability): LlmCall + LlmMetrics collector"
```

---

### Task 3: `LlmMetricsCallback` (latencja + usage + log bez PII)

**Files:**
- Modify: `src/invoicer/observability.py`
- Test: `tests/unit/test_observability.py`

- [ ] **Step 1: Write the failing tests**

Dopisz do `tests/unit/test_observability.py` (na końcu). Importy zdarzeń LangChain idą na górze pliku — dla czytelności trzymaj je razem z resztą importów na początku, ale wstawienie tutaj też zadziała:

```python
import logging

from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, LLMResult

from invoicer.observability import LlmMetricsCallback


def _llm_result(usage):
    """Syntetyczny LLMResult z wiadomoscia czatu; usage=None -> brak usage_metadata."""
    msg = AIMessage(content="ok") if usage is None else AIMessage(content="ok", usage_metadata=usage)
    return LLMResult(generations=[[ChatGeneration(message=msg)]])


def test_callback_records_call_with_tokens_cost_latency():
    metrics = LlmMetrics()
    clock = iter([10.0, 11.5]).__next__  # start=10.0, end=11.5 -> 1500 ms
    cb = LlmMetricsCallback(metrics, model="claude-sonnet-4-6", clock=clock)
    cb.on_chat_model_start({}, [], run_id="r1")
    cb.on_llm_end(
        _llm_result({"input_tokens": 654, "output_tokens": 35, "total_tokens": 689}),
        run_id="r1",
    )
    assert len(metrics.calls) == 1
    call = metrics.calls[0]
    assert call.model == "claude-sonnet-4-6"
    assert call.input_tokens == 654
    assert call.output_tokens == 35
    assert call.latency_ms == 1500
    assert call.cost_usd == pytest.approx(654 / 1_000_000 * 3 + 35 / 1_000_000 * 15)


def test_callback_handles_missing_usage_metadata():
    metrics = LlmMetrics()
    clock = iter([0.0, 0.25]).__next__
    cb = LlmMetricsCallback(metrics, model="claude-sonnet-4-6", clock=clock)
    cb.on_chat_model_start({}, [], run_id="r2")
    cb.on_llm_end(_llm_result(None), run_id="r2")
    call = metrics.calls[0]
    assert call.input_tokens == 0
    assert call.output_tokens == 0
    assert call.cost_usd == 0.0
    assert call.latency_ms == 250


def test_callback_via_on_llm_start_path():
    # sciezka non-chat: on_llm_start zamiast on_chat_model_start
    metrics = LlmMetrics()
    clock = iter([1.0, 2.0]).__next__
    cb = LlmMetricsCallback(metrics, model="claude-sonnet-4-6", clock=clock)
    cb.on_llm_start({}, ["prompt"], run_id="r4")
    cb.on_llm_end(_llm_result({"input_tokens": 10, "output_tokens": 5}), run_id="r4")
    assert metrics.calls[0].latency_ms == 1000


def test_callback_aggregates_multiple_calls():
    metrics = LlmMetrics()
    clock = iter([0.0, 1.0, 5.0, 6.0]).__next__
    cb = LlmMetricsCallback(metrics, model="claude-sonnet-4-6", clock=clock)
    cb.on_chat_model_start({}, [], run_id="a")
    cb.on_llm_end(_llm_result({"input_tokens": 100, "output_tokens": 10}), run_id="a")
    cb.on_chat_model_start({}, [], run_id="b")
    cb.on_llm_end(_llm_result({"input_tokens": 200, "output_tokens": 20}), run_id="b")
    t = metrics.totals()
    assert t["n_calls"] == 2
    assert t["input_tokens"] == 300
    assert t["output_tokens"] == 30


def test_callback_logs_metrics_without_pii(caplog):
    metrics = LlmMetrics()
    clock = iter([0.0, 0.1]).__next__
    cb = LlmMetricsCallback(metrics, model="claude-sonnet-4-6", clock=clock)
    with caplog.at_level(logging.INFO, logger="invoicer.metrics"):
        cb.on_chat_model_start({}, [], run_id="r3")
        cb.on_llm_end(_llm_result({"input_tokens": 654, "output_tokens": 35}), run_id="r3")
    text = caplog.text
    assert "llm_call" in text
    assert "model=claude-sonnet-4-6" in text
    assert "input_tokens=654" in text
    assert "output_tokens=35" in text
    assert "cost_usd=" in text
    assert "latency_ms=" in text
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/test_observability.py -v`
Expected: FAIL — `ImportError: cannot import name 'LlmMetricsCallback'`

- [ ] **Step 3: Add the callback and usage helper**

W `src/invoicer/observability.py` dodaj importy (pod istniejące `from __future__` i `from dataclasses import dataclass`):

```python
import logging
import time
from collections.abc import Callable

from langchain_core.callbacks import BaseCallbackHandler
```

Dodaj stałą z nazwą loggera tuż nad `_PRICING`:

```python
_METRICS_LOGGER = "invoicer.metrics"
```

Na końcu pliku dodaj helper i callback:

```python
def _usage_from_response(response) -> tuple[int, int]:
    """Wyciaga (input_tokens, output_tokens) z LLMResult; brak danych -> (0, 0)."""
    try:
        message = response.generations[0][0].message
    except (AttributeError, IndexError):
        return 0, 0
    usage = getattr(message, "usage_metadata", None)
    if not usage:
        return 0, 0
    return int(usage.get("input_tokens", 0) or 0), int(usage.get("output_tokens", 0) or 0)


class LlmMetricsCallback(BaseCallbackHandler):
    """Mierzy latencje (per run_id) i koszt kazdego wywolania LLM; zapis do kolektora + log (bez PII)."""

    def __init__(
        self,
        metrics: LlmMetrics,
        *,
        model: str,
        clock: Callable[[], float] = time.monotonic,
        logger: logging.Logger | None = None,
    ) -> None:
        self._metrics = metrics
        self._model = model
        self._clock = clock
        self._logger = logger or logging.getLogger(_METRICS_LOGGER)
        self._starts: dict = {}

    def on_chat_model_start(self, serialized, messages, *, run_id, **kwargs) -> None:
        self._starts[run_id] = self._clock()

    def on_llm_start(self, serialized, prompts, *, run_id, **kwargs) -> None:
        self._starts[run_id] = self._clock()

    def on_llm_end(self, response, *, run_id, **kwargs) -> None:
        start = self._starts.pop(run_id, None)
        end = self._clock()
        latency_ms = round((end - start) * 1000) if start is not None else 0

        input_tokens, output_tokens = _usage_from_response(response)
        cost = estimate_cost(self._model, input_tokens, output_tokens)
        self._metrics.record(
            LlmCall(
                model=self._model,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                cost_usd=cost,
                latency_ms=latency_ms,
            )
        )
        self._logger.info(
            "llm_call model=%s input_tokens=%d output_tokens=%d cost_usd=%.6f latency_ms=%d",
            self._model,
            input_tokens,
            output_tokens,
            cost,
            latency_ms,
        )
```

Uwaga implementacyjna: w `on_llm_end` używamy `self._starts.pop(run_id, None)` + osobnego `end = self._clock()` (a NIE `pop(run_id, self._clock())`) — `dict.pop` ewaluuje domyślną wartość zachłannie i niepotrzebnie skonsumowałoby tik zegara, psując deterministyczny zegar w testach.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_observability.py -v`
Expected: PASS (11 passed)

- [ ] **Step 5: Commit**

```bash
git add src/invoicer/observability.py tests/unit/test_observability.py
git commit -m "feat(observability): LlmMetricsCallback (latency + usage + PII-free log)"
```

---

### Task 4: Wpięcie `callbacks` w 3 adaptery Claude

**Files:**
- Modify: `src/invoicer/adapters/claude_extractor.py` (`__init__` ~54, `_client` ~62)
- Modify: `src/invoicer/adapters/claude_detector.py` (`__init__` ~45, `_client` ~53)
- Modify: `src/invoicer/adapters/claude_reasoner.py` (`__init__` ~48, `_client` ~56)
- Test: `tests/unit/test_metrics_wiring.py`

- [ ] **Step 1: Write the failing tests**

Utwórz `tests/unit/test_metrics_wiring.py`:

```python
import langchain_anthropic
import pytest

from invoicer.adapters.claude_detector import ClaudeInvoiceDetector
from invoicer.adapters.claude_extractor import ClaudeVisionExtractor
from invoicer.adapters.claude_reasoner import ClaudeExceptionReasoner
from invoicer.observability import LlmMetrics, LlmMetricsCallback

_FACTORIES = [ClaudeVisionExtractor, ClaudeInvoiceDetector, ClaudeExceptionReasoner]


class _RecordingChat:
    """Podstawiany w miejsce ChatAnthropic — zapamietuje kwargs konstruktora."""

    last_kwargs: dict = {}

    def __init__(self, **kwargs):
        _RecordingChat.last_kwargs = kwargs

    def with_structured_output(self, schema):
        return self


def _cb() -> LlmMetricsCallback:
    return LlmMetricsCallback(LlmMetrics(), model="claude-sonnet-4-6")


@pytest.mark.parametrize("factory", _FACTORIES)
def test_adapter_passes_callbacks_to_chatanthropic(monkeypatch, factory):
    monkeypatch.setattr(langchain_anthropic, "ChatAnthropic", _RecordingChat)
    cb = _cb()
    factory(callbacks=[cb])._client()
    assert _RecordingChat.last_kwargs["model"] == "claude-sonnet-4-6"
    assert _RecordingChat.last_kwargs["callbacks"] == [cb]


@pytest.mark.parametrize("factory", _FACTORIES)
def test_adapter_default_callbacks_is_none(monkeypatch, factory):
    monkeypatch.setattr(langchain_anthropic, "ChatAnthropic", _RecordingChat)
    factory()._client()
    assert _RecordingChat.last_kwargs["callbacks"] is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/test_metrics_wiring.py -v`
Expected: FAIL — `TypeError: __init__() got an unexpected keyword argument 'callbacks'`

- [ ] **Step 3: Add `callbacks` to `ClaudeVisionExtractor`**

W `src/invoicer/adapters/claude_extractor.py` zmień `__init__` i `_client`:

```python
    def __init__(self, *, model: str = _DEFAULT_MODEL, llm: Any = None,
                 callbacks: list | None = None) -> None:
        self._model = model
        self._llm = llm
        self._callbacks = callbacks
```

```python
    def _client(self):
        if self._llm is None:
            from langchain_anthropic import ChatAnthropic

            self._llm = ChatAnthropic(model=self._model, callbacks=self._callbacks)
        return self._llm
```

- [ ] **Step 4: Add `callbacks` to `ClaudeInvoiceDetector`**

W `src/invoicer/adapters/claude_detector.py` zmień `__init__` i `_client`:

```python
    def __init__(self, *, model: str = _DEFAULT_MODEL, llm: Any = None,
                 callbacks: list | None = None) -> None:
        self._model = model
        self._llm = llm
        self._callbacks = callbacks
```

```python
    def _client(self):
        if self._llm is None:
            from langchain_anthropic import ChatAnthropic

            self._llm = ChatAnthropic(model=self._model, callbacks=self._callbacks)
        return self._llm
```

- [ ] **Step 5: Add `callbacks` to `ClaudeExceptionReasoner`**

Najpierw potwierdź wzorzec `_client` w tym pliku (powinien być identyczny):

Run: `sed -n '48,60p' src/invoicer/adapters/claude_reasoner.py`
Expected: `__init__` z `model`/`llm` i `_client` budujący `ChatAnthropic(model=self._model)`.

W `src/invoicer/adapters/claude_reasoner.py` zmień `__init__` i `_client` tak samo:

```python
    def __init__(self, *, model: str = _DEFAULT_MODEL, llm: Any = None,
                 callbacks: list | None = None) -> None:
        self._model = model
        self._llm = llm
        self._callbacks = callbacks
```

```python
    def _client(self):
        if self._llm is None:
            from langchain_anthropic import ChatAnthropic

            self._llm = ChatAnthropic(model=self._model, callbacks=self._callbacks)
        return self._llm
```

- [ ] **Step 6: Run wiring tests to verify they pass**

Run: `uv run pytest tests/unit/test_metrics_wiring.py -v`
Expected: PASS (6 passed)

- [ ] **Step 7: Run existing adapter tests to confirm no regression**

Run: `uv run pytest tests/unit/test_claude_extractor.py tests/unit/test_invoice_detector.py tests/unit/test_claude_reasoner.py -q`
Expected: PASS (wszystkie istniejące fake-llm testy zielone — wstrzyknięty `llm` pomija ścieżkę `ChatAnthropic`)

- [ ] **Step 8: Commit**

```bash
git add src/invoicer/adapters/claude_extractor.py src/invoicer/adapters/claude_detector.py src/invoicer/adapters/claude_reasoner.py tests/unit/test_metrics_wiring.py
git commit -m "feat(adapters): opt-in callbacks= passthrough to ChatAnthropic"
```

---

### Task 5: Test live-gated (realny call → metryki)

**Files:**
- Create: `tests/live/test_metrics_live.py`

- [ ] **Step 1: Write the live-gated test**

Utwórz `tests/live/test_metrics_live.py` (gating jak w `tests/live/test_invoice_detector_live.py`):

```python
import os
from datetime import datetime
from pathlib import Path

import pytest

from invoicer.adapters.claude_detector import ClaudeInvoiceDetector
from invoicer.models import InvoiceDocument
from invoicer.observability import LlmMetrics, LlmMetricsCallback

_FIXTURE = Path(__file__).parent / "fixtures" / "sample_invoice.pdf"

pytestmark = pytest.mark.skipif(
    not os.getenv("ANTHROPIC_API_KEY") or not _FIXTURE.exists(),
    reason="wymaga ANTHROPIC_API_KEY oraz tests/live/fixtures/sample_invoice.pdf (test live)",
)


def test_metrics_captured_on_real_detection():
    metrics = LlmMetrics()
    cb = LlmMetricsCallback(metrics, model="claude-sonnet-4-6")
    detector = ClaudeInvoiceDetector(callbacks=[cb])
    doc = InvoiceDocument(
        sender="a@b.pl",
        received_at=datetime(2026, 6, 23),
        filename="sample_invoice.pdf",
        content=_FIXTURE.read_bytes(),
    )
    detector.is_invoice(doc)
    assert metrics.calls, "callback nie zarejestrowal zadnego wywolania"
    call = metrics.calls[0]
    assert call.model == "claude-sonnet-4-6"
    assert call.input_tokens > 0
    assert call.output_tokens > 0
    assert call.cost_usd > 0
    assert call.latency_ms > 0
```

- [ ] **Step 2: Verify it is collected and skips without credentials**

Run: `uv run pytest tests/live/test_metrics_live.py -v`
Expected: `1 skipped` (gdy brak `ANTHROPIC_API_KEY` lub fixture) — zebranie testu bez błędu importu. Z kluczem + fixture: PASS.

- [ ] **Step 3: Commit**

```bash
git add tests/live/test_metrics_live.py
git commit -m "test(live): metrics captured on real detection (gated)"
```

---

### Task 6: Lint + pełny suite (zielona baza)

**Files:** brak nowych — weryfikacja całości.

- [ ] **Step 1: Ruff lint**

Run: `uv run ruff check .`
Expected: `All checks passed!`

- [ ] **Step 2: Ruff format check**

Run: `uv run ruff format --check .`
Expected: brak plików do przeformatowania. Jeśli ruff zgłosi zmiany: `uv run ruff format .`, potem ponów krok 1.

- [ ] **Step 3: Full test suite**

Run: `uv run pytest -q`
Expected: wszystkie zielone — baseline 186 passed + nowe testy (~19), 6 skipped + `test_metrics_live` skipped bez klucza. Zero failed.

- [ ] **Step 4: Commit (jeśli ruff format coś zmienił; inaczej pomiń)**

```bash
git add -A
git commit -m "chore(observability): ruff format"
```

Jeśli nic do scommitowania (`git status` czysty) — pomiń ten krok.

---

## Po wykonaniu planu

Finałowy review (opus) całego brancha `feat/llm-metrics`, a następnie `git checkout main && git merge --no-ff feat/llm-metrics` (zgodnie z przyjętym przepływem per-feature).
