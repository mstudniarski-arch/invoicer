# Plan 2 — Observability (Sentry + alerty) + CI/CD — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Domknac observability: Sentry (errory ze stack-trace + alerty mailowe, PII scrubowane) + proaktywny alert o porazce na WhatsApp + CI/CD auto-deploy na Fly po zielonym CI na `main`.

**Architecture:** Dwa nowe plaskie moduly `observability_sentry.py` (init + scrub PII) i `observability_alerts.py` (wysylka alertu, nigdy nie rzuca). Sentry auto-lapie logi ERROR+ (LoggingIntegration) — `before_send`/`before_breadcrumb` scrubuja PII (logi i tak redagowane na poziomie handlera, ale handler Sentry jest osobny, wiec scrub jest tu bramka PII). Alerty WhatsApp wpinane callbackami: `run_daily_intake(alert=...)` (zaciag) i `create_inbound_app(on_resume_failure=...)` (webhook). `create_app` okabla wszystko (Sentry z `SENTRY_DSN`, kanal Twilio eagerly poza test_mode). CI/CD: nowy workflow `deploy.yml` (trigger `workflow_run` po sukcesie "CI" na `main`) -> `flyctl deploy`.

**Tech Stack:** Python 3, `uv`, `pytest`, `ruff`; `sentry-sdk` (nowa dep); GitHub Actions + `superfly/flyctl-actions`. Reuzywa `TwilioWhatsAppChannel.notify` (Plan 1, Task 1B), `redact_pii` (security.py).

**Spec:** `docs/superpowers/specs/2026-06-24-deployment-observability-design.md` (§7, §8, §11 — Plan 2).

**Branch:** `feat/deploy-plan2-observability` (z `main` po merge Planu 1). Baseline: 222 passed / 7 skipped, ruff czysty.

---

## File Structure

| Plik | Odpowiedzialnosc | Akcja |
|------|------------------|-------|
| `src/invoicer/observability_sentry.py` | `init_sentry(dsn)` (no-op bez DSN) + `_scrub`/`_redact_obj` (rekursywna redakcja PII w evencie/breadcrumb) | Create (Task 1) |
| `src/invoicer/observability_alerts.py` | `format_failure_alert(context, reason)` + `send_failure_alert(channel, text)` (try/except — nigdy nie rzuca) | Create (Task 2) |
| `src/invoicer/scheduler.py` | + param `alert: Callable[[str, str], None]` w `run_daily_intake`; wolany przy porazce faktury | Modify (Task 3) |
| `src/invoicer/webhook.py` | + param `on_resume_failure: Callable[[str, Exception], None] \| None` w `create_inbound_app`; wolany w branchu `resume_failed` | Modify (Task 4) |
| `src/invoicer/app.py` | Okablowanie: `init_sentry(SENTRY_DSN)` + kanal Twilio eagerly (poza test_mode) + przekazanie alertow do schedulera i webhooka | Modify (Task 5) |
| `.github/workflows/deploy.yml` | Auto-deploy na Fly po zielonym CI na `main` (`workflow_run`) | Create (Task 6) |
| `README.md` | + sekcja CI/CD (FLY_API_TOKEN w sekretach GH) | Modify (Task 7) |
| `pyproject.toml` | + `sentry-sdk` | Modify (Task 0) |
| testy: `test_observability_sentry.py`, `test_observability_alerts.py` (nowe) + rozszerzenia `test_scheduler.py`, `test_webhook.py`, `test_app.py` | | Create/Modify |

Komendy uruchamiac z `/Users/mski/Developer/Invoicer`. `pytest` ma `pythonpath=["src"]`.

---

### Task 0: Branch + zaleznosc `sentry-sdk`

**Files:**
- Modify: `pyproject.toml`

- [ ] **Step 1: Potwierdz branch + baseline**

```bash
git checkout feat/deploy-plan2-observability
uv run pytest -q
```
Expected: `222 passed, 7 skipped`.

- [ ] **Step 2: Dodaj dep (alfabetycznie, po `python-multipart`, przed `streamlit`)**

W `pyproject.toml`, w `dependencies = [...]`, dodaj linie:

```toml
    "sentry-sdk>=2.0",
```

- [ ] **Step 3: Sync + sanity**

```bash
uv sync
uv run python -c "import sentry_sdk; print('sentry', sentry_sdk.VERSION)"
```
Expected: wypisuje wersje (>=2.0).

- [ ] **Step 4: Commit**

```bash
git add pyproject.toml uv.lock
git commit -m "deps(observability): add sentry-sdk for error tracking"
```

---

### Task 1: `observability_sentry.py` — init + scrub PII

**Files:**
- Create: `src/invoicer/observability_sentry.py`
- Test: `tests/unit/test_observability_sentry.py`

- [ ] **Step 1: Failing test**

Utworz `tests/unit/test_observability_sentry.py`:

```python
from __future__ import annotations

from invoicer.observability_sentry import _scrub, init_sentry


def test_init_sentry_noop_without_dsn():
    assert init_sentry(None) is False
    assert init_sentry("") is False


def test_scrub_redacts_pii_in_nested_event():
    event = {
        "logentry": {"message": "blad faktury NIP 5260001246"},
        "exception": {"values": [{"value": "kontakt ksiegowa@firma.pl"}]},
        "extra": {"iban": "PL61109010140000071219812874"},
        "level": "error",
    }
    out = _scrub(event, None)
    flat = str(out)
    assert "5260001246" not in flat
    assert "ksiegowa@firma.pl" not in flat
    assert "PL61109010140000071219812874" not in flat
    assert "[NIP]" in flat and "[EMAIL]" in flat and "[KONTO]" in flat
    assert out["level"] == "error"  # nie-stringi nietkniete


def test_init_sentry_calls_sdk_with_scrub(monkeypatch):
    import sentry_sdk

    captured = {}
    monkeypatch.setattr(sentry_sdk, "init", lambda **kw: captured.update(kw))
    assert init_sentry("https://abc@o1.ingest.sentry.io/1") is True
    assert captured["before_send"] is _scrub
    assert captured["send_default_pii"] is False
```

- [ ] **Step 2: Verify fail**

```bash
uv run pytest tests/unit/test_observability_sentry.py -q
```
Expected: FAIL — `ModuleNotFoundError: No module named 'invoicer.observability_sentry'`.

- [ ] **Step 3: Implement**

Utworz `src/invoicer/observability_sentry.py`:

```python
from __future__ import annotations

from typing import Any

from invoicer.security import redact_pii


def _redact_obj(obj: Any) -> Any:
    """Rekursywnie redaguje PII we wszystkich stringach (dict/list/tuple/str)."""
    if isinstance(obj, str):
        return redact_pii(obj)
    if isinstance(obj, dict):
        return {k: _redact_obj(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_redact_obj(v) for v in obj]
    if isinstance(obj, tuple):
        return tuple(_redact_obj(v) for v in obj)
    return obj


def _scrub(event: dict, hint: Any) -> dict:
    """before_send: scrubuje PII z calego eventu Sentry."""
    return _redact_obj(event)


def init_sentry(dsn: str | None) -> bool:
    """Init Sentry z redakcja PII (before_send + before_breadcrumb). No-op bez DSN.

    Sentry domyslnie lapie logi ERROR+ jako eventy (LoggingIntegration); handler Sentry
    jest osobny od RedactingFilter, wiec scrub PII robimy tu (bramka). Zwraca True gdy zainit.
    """
    if not dsn:
        return False
    import sentry_sdk

    sentry_sdk.init(
        dsn=dsn,
        before_send=_scrub,
        before_breadcrumb=lambda crumb, hint: _redact_obj(crumb),
        send_default_pii=False,
        traces_sample_rate=0.0,
    )
    return True
```

- [ ] **Step 4: Verify pass**

```bash
uv run pytest tests/unit/test_observability_sentry.py -q
```
Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add src/invoicer/observability_sentry.py tests/unit/test_observability_sentry.py
git commit -m "feat(observability): Sentry init + PII-scrubbing before_send/before_breadcrumb"
```

---

### Task 2: `observability_alerts.py` — alert o porazce

**Files:**
- Create: `src/invoicer/observability_alerts.py`
- Test: `tests/unit/test_observability_alerts.py`

- [ ] **Step 1: Failing test**

Utworz `tests/unit/test_observability_alerts.py`:

```python
from __future__ import annotations

from invoicer.observability_alerts import format_failure_alert, send_failure_alert


class _FakeChannel:
    def __init__(self, *, boom: bool = False):
        self.sent: list[str] = []
        self._boom = boom

    def notify(self, text: str) -> None:
        if self._boom:
            raise RuntimeError("twilio down")
        self.sent.append(text)


def test_format_failure_alert():
    msg = format_failure_alert("faktura.pdf", "ekstrakcja padla")
    assert msg.startswith("⚠️")
    assert "faktura.pdf" in msg
    assert "ekstrakcja padla" in msg


def test_send_failure_alert_delivers():
    ch = _FakeChannel()
    send_failure_alert(ch, "⚠️ test")
    assert ch.sent == ["⚠️ test"]


def test_send_failure_alert_never_raises_when_channel_fails():
    ch = _FakeChannel(boom=True)
    # alert nie moze wywalic pipeline'u — blad kanalu jest polykany
    send_failure_alert(ch, "⚠️ test")  # nie rzuca
```

- [ ] **Step 2: Verify fail**

```bash
uv run pytest tests/unit/test_observability_alerts.py -q
```
Expected: FAIL — `ModuleNotFoundError: No module named 'invoicer.observability_alerts'`.

- [ ] **Step 3: Implement**

Utworz `src/invoicer/observability_alerts.py`:

```python
from __future__ import annotations

import logging
from typing import Any

from invoicer.security import redact_pii

_logger = logging.getLogger("invoicer.alerts")


def format_failure_alert(context: str, reason: str) -> str:
    """Krotka tresc alertu o porazce (idzie na WhatsApp wlasciciela)."""
    return f"⚠️ {context}: {reason}"


def send_failure_alert(channel: Any, text: str) -> None:
    """Wysyla alert przez kanal (channel.notify). NIGDY nie rzuca — blad kanalu tylko logujemy."""
    try:
        channel.notify(text)
    except Exception as exc:  # noqa: BLE001 - alert nie moze wywalic pipeline'u
        _logger.error("alert nieudany: %s", redact_pii(str(exc)))
```

- [ ] **Step 4: Verify pass**

```bash
uv run pytest tests/unit/test_observability_alerts.py -q
```
Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add src/invoicer/observability_alerts.py tests/unit/test_observability_alerts.py
git commit -m "feat(observability): send_failure_alert (never raises) + format_failure_alert"
```

---

### Task 3: Wpiecie alertu w `run_daily_intake`

**Files:**
- Modify: `src/invoicer/scheduler.py`
- Test: `tests/unit/test_scheduler.py`

- [ ] **Step 1: Failing test (dopisz na koncu `tests/unit/test_scheduler.py`)**

```python
def test_run_daily_intake_calls_alert_on_failure():
    docs = [_doc("a.pdf"), _doc("b.pdf")]
    channel = StubApprovalChannel()
    counters = PipelineCounters()
    alerts: list[tuple[str, str]] = []

    def request_fn(graph, channel_, registry, document, *, thread_id, phone):
        if document.filename == "b.pdf":
            raise RuntimeError("ekstrakcja padla")
        channel_.request_approval({"x": document.filename})
        return {"x": document.filename}

    run_daily_intake(
        MagicMock(),
        channel,
        MagicMock(),
        _FakeSource(docs),
        StubInvoiceDetector(result=True),
        sender="owner@example.com",
        phone="whatsapp:+48111",
        counters=counters,
        request_fn=request_fn,
        alert=lambda ctx, reason: alerts.append((ctx, reason)),
    )
    assert len(alerts) == 1
    assert alerts[0][0] == "b.pdf"
    assert "ekstrakcja padla" in alerts[0][1]
```

- [ ] **Step 2: Verify fail**

```bash
uv run pytest tests/unit/test_scheduler.py::test_run_daily_intake_calls_alert_on_failure -q
```
Expected: FAIL — `TypeError: run_daily_intake() got an unexpected keyword argument 'alert'`.

- [ ] **Step 3: Implement — dodaj param `alert` i wolaj go przy porazce**

W `src/invoicer/scheduler.py` zmien sygnature `run_daily_intake` i petle.

Zmien sygnature (dodaj `alert` jako ostatni keyword-only param):

```python
def run_daily_intake(
    graph: Any,
    channel: Any,
    registry: Any,
    source: EmailSource,
    detector: InvoiceDetector,
    *,
    sender: str,
    phone: str,
    counters: PipelineCounters,
    request_fn: Callable[..., dict | None] = request_invoice_approval,
    alert: Callable[[str, str], None] = lambda *_: None,
) -> None:
```

W bloku `except` (w petli) dodaj wolanie `alert(...)` PO `_logger.exception(...)`:

```python
        except Exception as exc:  # noqa: BLE001 - jedna zla faktura nie blokuje pozostalych
            counters.incr_failed()
            _logger.exception("intake: faktura %s nie przeszla", doc.filename)
            alert(doc.filename, str(exc))
```

(Reszta funkcji bez zmian. Uwaga: komentarz przy `except` zostaje — tylko dodajemy linie `alert(...)`.)

- [ ] **Step 4: Verify pass**

```bash
uv run pytest tests/unit/test_scheduler.py -q
```
Expected: 4 passed (3 istniejace + 1 nowy).

- [ ] **Step 5: Commit**

```bash
git add src/invoicer/scheduler.py tests/unit/test_scheduler.py
git commit -m "feat(scheduler): alert callback on per-invoice failure"
```

---

### Task 4: Wpiecie `on_resume_failure` w webhook

**Files:**
- Modify: `src/invoicer/webhook.py`
- Test: `tests/unit/test_webhook.py`

- [ ] **Step 1: Failing test (dopisz na koncu `tests/unit/test_webhook.py`)**

```python
def test_inbound_calls_on_resume_failure_and_returns_2xx():
    from fastapi.testclient import TestClient

    from invoicer.webhook import create_inbound_app

    class _Registry:
        def resolve_oldest(self, phone):
            return "thread-1"

    def boom_resume(graph, *, thread_id, decision):
        raise RuntimeError("ksiegowanie padlo")

    captured: list[tuple[str, str]] = []

    app = create_inbound_app(
        object(),
        _Registry(),
        resume=boom_resume,
        on_resume_failure=lambda thread_id, exc: captured.append((thread_id, str(exc))),
    )
    client = TestClient(app)
    r = client.post("/whatsapp/inbound", data={"From": "whatsapp:+48111", "Body": "TAK"})
    assert r.status_code == 200
    assert r.json()["status"] == "resume_failed"
    assert captured == [("thread-1", "ksiegowanie padlo")]
```

- [ ] **Step 2: Verify fail**

```bash
uv run pytest tests/unit/test_webhook.py::test_inbound_calls_on_resume_failure_and_returns_2xx -q
```
Expected: FAIL — `TypeError: create_inbound_app() got an unexpected keyword argument 'on_resume_failure'`.

- [ ] **Step 3: Implement**

W `src/invoicer/webhook.py`:

1) Dodaj import na gorze (pod istniejacymi importami):

```python
from collections.abc import Callable
```

2) Zmien sygnature `create_inbound_app`:

```python
def create_inbound_app(
    graph,
    registry,
    *,
    resume=resume_document,
    on_resume_failure: Callable[[str, Exception], None] | None = None,
) -> FastAPI:
```

3) W branchu `except` (po istniejacym `_logger.error(...)`, przed `return`) wywolaj callback:

```python
        try:
            resume(graph, thread_id=thread_id, decision=decision)
        except Exception as exc:  # noqa: BLE001 - webhook musi zwrocic 2xx (brak retry-storm Twilio)
            _logger.error("resume nieudany dla %s: %s", thread_id, redact_pii(str(exc)))
            if on_resume_failure is not None:
                on_resume_failure(thread_id, exc)
            return {"status": "resume_failed", "thread_id": thread_id}
```

(Docstring mozesz uzupelnic jednym zdaniem o `on_resume_failure`. Reszta bez zmian.)

- [ ] **Step 4: Verify pass**

```bash
uv run pytest tests/unit/test_webhook.py -q
```
Expected: wszystkie istniejace + 1 nowy zielone.

- [ ] **Step 5: Commit**

```bash
git add src/invoicer/webhook.py tests/unit/test_webhook.py
git commit -m "feat(webhook): on_resume_failure callback (alert/Sentry hook), still 2xx"
```

---

### Task 5: Okablowanie w `app.py` (Sentry + kanal + alerty)

**Files:**
- Modify: `src/invoicer/app.py`
- Test: `tests/unit/test_app.py`

- [ ] **Step 1: Failing test (dopisz na koncu `tests/unit/test_app.py`)**

```python
def test_sentry_not_initialized_in_test_mode(tmp_path, monkeypatch):
    import invoicer.app as appmod

    calls: list[str | None] = []
    monkeypatch.setattr(appmod, "init_sentry", lambda dsn: calls.append(dsn) or False)
    app = create_app(settings=_settings(tmp_path))
    # test_mode: Sentry NIE jest inicjalizowany (brak realnych adapterow/sekretow)
    assert calls == []
    with TestClient(app) as client:
        assert client.get("/health").status_code == 200
```

- [ ] **Step 2: Verify fail**

```bash
uv run pytest tests/unit/test_app.py::test_sentry_not_initialized_in_test_mode -q
```
Expected: FAIL — `AttributeError: <module 'invoicer.app'> does not have the attribute 'init_sentry'` (jeszcze nie zaimportowany).

- [ ] **Step 3: Implement — okablowanie**

W `src/invoicer/app.py`:

1) Dodaj importy (pod istniejacymi `from invoicer....`):

```python
from invoicer.observability_alerts import format_failure_alert, send_failure_alert
from invoicer.observability_sentry import init_sentry
```

2) W `create_app`, w bloku `if not settings.test_mode:` (tam gdzie jest `bootstrap_gmail_token`), dodaj init Sentry i budowe kanalu + closure porazki. Zamien istniejacy blok:

```python
    if not settings.test_mode:
        bootstrap_gmail_token("GMAIL_TOKEN_B64", settings.data_dir / "token.json")
```

na:

```python
    channel = None
    on_resume_failure = None
    if not settings.test_mode:
        init_sentry(os.getenv("SENTRY_DSN"))
        bootstrap_gmail_token("GMAIL_TOKEN_B64", settings.data_dir / "token.json")
        from invoicer.adapters.twilio_whatsapp import build_twilio_whatsapp_channel

        channel = build_twilio_whatsapp_channel()

        def on_resume_failure(thread_id: str, exc: Exception) -> None:
            send_failure_alert(
                channel, format_failure_alert(f"watek {thread_id}", str(exc))
            )
```

3) Zmien wolanie `create_inbound_app(graph, registry)` na:

```python
    app = create_inbound_app(graph, registry, on_resume_failure=on_resume_failure)
```

4) W `_lifespan._job`, usun budowanie kanalu wewnatrz (kanal jest juz z `create_app`) i przekaz `alert`. Zamien cialo lifespanu — zamiast `channel = build_twilio_whatsapp_channel()` w lifespanie, uzyj `channel` z domkniecia i dodaj `alert=` w `run_daily_intake`:

```python
    @asynccontextmanager
    async def _lifespan(_: FastAPI):
        from invoicer.adapters.claude_detector import ClaudeInvoiceDetector
        from invoicer.adapters.gmail import GmailAdapter
        from invoicer.adapters.gmail_auth import gmail_service_from_token

        def _job() -> None:
            service = gmail_service_from_token(settings.data_dir / "token.json")
            run_daily_intake(
                graph,
                channel,
                registry,
                GmailAdapter(service),
                ClaudeInvoiceDetector(),
                sender=settings.gmail_sender,
                phone=settings.approver_phone,
                counters=counters,
                alert=lambda ctx, reason: send_failure_alert(
                    channel, format_failure_alert(ctx, reason)
                ),
            )

        scheduler = build_scheduler(
            _job,
            hour=settings.intake_hour,
            minute=settings.intake_minute,
            tz=settings.intake_tz,
        )
        scheduler.start()
        try:
            yield
        finally:
            scheduler.shutdown(wait=False)
```

(Usunieto import `build_twilio_whatsapp_channel` z lifespanu — teraz jest w bloku non-test wyzej.)

- [ ] **Step 4: Verify pass + brak regresji**

```bash
uv run pytest tests/unit/test_app.py -q
uv run ruff check src/invoicer/app.py
```
Expected: 5 passed (4 istniejace + 1 nowy); ruff czysty.

- [ ] **Step 5: Commit**

```bash
git add src/invoicer/app.py tests/unit/test_app.py
git commit -m "feat(app): wire Sentry + WhatsApp failure alerts (scheduler + webhook)"
```

---

### Task 6: `.github/workflows/deploy.yml` — CI/CD auto-deploy

**Files:**
- Create: `.github/workflows/deploy.yml`

- [ ] **Step 1: Utworz workflow**

Plik `.github/workflows/deploy.yml`:

```yaml
name: Deploy

# Auto-deploy na Fly PO zielonym workflow "CI" na galezi main (bez duplikacji testow).
on:
  workflow_run:
    workflows: ["CI"]
    types: [completed]
    branches: [main]

jobs:
  deploy:
    if: ${{ github.event.workflow_run.conclusion == 'success' }}
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: superfly/flyctl-actions/setup-flyctl@master
      - name: Deploy to Fly
        run: flyctl deploy --remote-only
        env:
          FLY_API_TOKEN: ${{ secrets.FLY_API_TOKEN }}
```

- [ ] **Step 2: Weryfikacja (skladnia + kluczowe pola)**

```bash
test -f .github/workflows/deploy.yml && echo "plik ok"
uv run python -c "import yaml,sys; yaml.safe_load(open('.github/workflows/deploy.yml')); print('yaml ok')"
grep -q "workflows: \[\"CI\"\]" .github/workflows/deploy.yml && echo "trigger CI ok"
grep -q "conclusion == 'success'" .github/workflows/deploy.yml && echo "gate green ok"
grep -q "flyctl deploy" .github/workflows/deploy.yml && echo "deploy ok"
grep -q "FLY_API_TOKEN" .github/workflows/deploy.yml && echo "token ok"
```
Expected: szesc "ok". (Jezeli `yaml` nie jest zainstalowany jako dep — pomin krok yaml; pozostale grepy wystarcza. `yaml` jest transitive przez wiele paczek, ale gdy brak: `uv run python -c "import yaml"` rzuci ImportError — wtedy uzyj samego `python -c "...json/grep"`; skladnie potwierdza grepy.)

- [ ] **Step 3: Commit**

```bash
git add .github/workflows/deploy.yml
git commit -m "ci(deploy): auto-deploy to Fly after green CI on main (workflow_run)"
```

---

### Task 7: README — sekcja CI/CD

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Dopisz pod-sekcje CI/CD w sekcji Deploy**

W `README.md`, w sekcji `## Deploy (Fly.io)`, ZASTAP pod-sekcje `### 4. Aktualizacje` cala trescia ponizej (rozszerza ja o CI/CD):

```markdown
### 4. Aktualizacje (CI/CD)

Auto-deploy: po zielonym CI na `main` workflow `deploy.yml` robi `flyctl deploy` (rolling restart;
stan na `/data` przezywa). Jednorazowo dodaj token Fly do sekretow GitHub:

```bash
fly tokens create deploy -x 999999h   # token deploy
# GitHub repo -> Settings -> Secrets and variables -> Actions -> New repository secret:
#   nazwa: FLY_API_TOKEN   wartosc: <powyzszy token>
```

Deploy reczny (gdy trzeba): `fly deploy`.
```

- [ ] **Step 2: Weryfikacja**

```bash
grep -q "Aktualizacje (CI/CD)" README.md && echo "ok"
grep -q "FLY_API_TOKEN" README.md && echo "ok"
grep -q "fly tokens create deploy" README.md && echo "ok"
```
Expected: trzy "ok".

- [ ] **Step 3: Commit**

```bash
git add README.md
git commit -m "docs(deploy): README — CI/CD auto-deploy + FLY_API_TOKEN setup"
```

---

### Task 8: Lint + pelny suite

**Files:** brak nowych — pelna weryfikacja.

- [ ] **Step 1: Ruff**

```bash
uv run ruff check .
uv run ruff format --check .
```
Expected: `All checks passed!` + brak plikow do przeformatowania (gdy chce: `uv run ruff format .`, potem powtorz).

- [ ] **Step 2: Full suite**

```bash
uv run pytest -q
```
Expected: zielone, **baseline 222 + ~9 nowych** (Task 1 +3, Task 2 +3, Task 3 +1, Task 4 +1, Task 5 +1). Zero failed.

- [ ] **Step 3: Commit (jezeli format cos zmienil; inaczej pomin)**

```bash
git status --short   # pusty -> pomin
git add -A
git commit -m "chore(observability): ruff format"
```

---

## Po wykonaniu planu

Finalowy review (opus) calej galezi `feat/deploy-plan2-observability`, potem `git checkout main && git merge --no-ff feat/deploy-plan2-observability`.

Manualnie (uzytkownik, po merge): `fly tokens create deploy` -> `FLY_API_TOKEN` w sekretach GH; `fly secrets set SENTRY_DSN=...` (z projektu Sentry); kolejny push do `main` wyzwoli auto-deploy.
