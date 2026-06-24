# Invoicer — Design: deploy 24/7 + observability (monitoring/logi/debug)

**Data:** 2026-06-24
**Status:** zatwierdzony projekt (realizacja subagent-driven; przy `writing-plans` podzial na 2 plany — patrz §11)
**Realizuje:** agent dziala 24/7 na PaaS (Fly.io) jako jeden zawsze-zywy serwis: codzienny zaciag faktur z Gmaila (scheduler) + zawsze-zywy webhook akceptacji WhatsApp; z monitoringiem (Sentry, /health, /status), strukturalnymi logami i proaktywnym alertem o porazce. Deploy przez CI/CD.

---

## 1. Problem / kontekst

Dzis agent to skrypty one-shot (`scripts/whatsapp_approval.py`, `scripts/whatsapp_webhook.py`, throwaway poll-runner): odpalane recznie, na maszynie uzytkownika, bez publicznego URL (polling Twilio jako obejscie braku ngroka), bez monitoringu i alertow. Cel: **produkcjonizacja** — agent ma sam zaciagac faktury i przyjmowac akceptacje 24/7, a w razie problemu ma byc widac CO i GDZIE peklo.

Pipeline (bez zmian): Gmail (dzienny .pdf) → ClaudeInvoiceDetector → ClaudeVisionExtractor → klasyfikacja PL → [reason_exception dla zagranicznych] → `human_review` (interrupt) → request WhatsApp → odpowiedz TAK/NIE → resume → FakturowniaSink (realne ksiegowanie).

**Co juz mamy i reuzywamy:** durable checkpointer (`persistent_checkpointer`, SQLite), `PendingApprovals` (rejestr FIFO), webhook Plan B (`create_inbound_app` — `POST /whatsapp/inbound`, 2xx przy porazce = brak retry-storm), `request_invoice_approval`, `fetch_invoice_documents`, metryki LLM (`LlmMetrics` + `LlmMetricsCallback`, logger `invoicer.metrics`), redakcja PII (`redact_pii` + `RedactingFilter` + `install_redaction`). CI (`.github/workflows/ci.yml`) zielone.

**Decyzje uzytkownika (brainstorming):** hosting = PaaS (Fly.io); model = jeden zawsze-zywy serwis + wbudowany scheduler; observability = Sentry + /health+/status + proaktywny alert o porazce (bez endpointu debug/replay); deploy = CI/CD (auto po merge do `main`).

---

## 2. Zakres

**W zakresie:**
- Fabryka aplikacji (`app.py`): jeden FastAPI/uvicorn z `POST /whatsapp/inbound` (realny webhook zamiast pollingu), `GET /health`, `GET /status`; lifespan startuje scheduler.
- Scheduler (`scheduler.py`): codzienny job zaciagu (APScheduler, cron, Europe/Warsaw).
- Observability: init Sentry (gated `SENTRY_DSN`, `before_send` scrubuje PII), `/status` (metryki LLM + liczniki pipeline'u), proaktywny alert o porazce na WhatsApp approvera.
- Konteneryzacja + deploy: `Dockerfile` (uv), `fly.toml` (1 maszyna always-on, wolumen `/data`, healthcheck), `.github/workflows/deploy.yml` (CI green na `main` → `flyctl deploy`).
- Sekrety przez `fly secrets`; Gmail `token.json` wstrzykiwany jako `GMAIL_TOKEN_B64` (headless OAuth).
- Nowe zaleznosci: `sentry-sdk`, `apscheduler`.
- Testy jednostkowe (TestClient na /health,/status; job zaciagu na stubach; alert przy porazce; `before_send` Sentry bez PII; wiring fabryki) + udokumentowany manualny smoke deploy.

**Poza zakresem (swiadome YAGNI):**
- Endpoint debug/replay (uzytkownik nie wybral) — pending/failed inspekcja na razie przez logi/Sentry/`/status`.
- Multi-user / wielu approverow / skalowanie poziome (jeden approver, jedna maszyna, jeden wolumen).
- Prometheus/Grafana/OTel — panele PaaS + `/status` + Sentry wystarczaja.
- Zmiana pipeline'u/grafu/modelu — nietkniete.
- Automatyczna konfiguracja webhooka w konsoli Twilio (URL `*.fly.dev/whatsapp/inbound` wkleja uzytkownik jednorazowo, jak wczesniej ngrok).
- Polling Twilio zostaje TYLKO jako lokalny fallback dev (nie na produkcji).

---

## 3. Architektura

Jeden kontener na Fly.io, jedna maszyna **always-on** (`auto_stop_machines=false`, `min_machines_running=1` — webhook i scheduler musza zyc), trwaly wolumen zamontowany na `/data`.

```
                         Fly.io machine (always-on)
   ┌───────────────────────────────────────────────────────────┐
   │  uvicorn  ──►  FastAPI (invoicer.app:app)                  │
   │    ├─ POST /whatsapp/inbound  ◄── Twilio (odpowiedz TAK/NIE)│
   │    ├─ GET  /health            ◄── Fly liveness check       │
   │    ├─ GET  /status            ◄── metryki LLM + liczniki    │
   │    └─ lifespan ─► APScheduler ── cron 08:00 Europe/Warsaw   │
   │                       │                                     │
   │              run_daily_intake()                            │
   │        Gmail → detect → extract → gate → WhatsApp request   │
   │                       │                                     │
   │   durable graf (Claude + FakturowniaSink + Twilio)         │
   │   SQLite /data/invoicer_state.sqlite  (checkpointer+pending)│
   │   /data/ledger.jsonl   /data/token.json                    │
   │   Sentry (errors+alerty)  ·  logi JSON → stdout → Fly logs  │
   └───────────────────────────────────────────────────────────┘
            ▲ deploy: GH Actions (CI green na main) → flyctl deploy
```

Webhook akceptacji jest zawsze zywy → **rezygnujemy z pollingu** (PaaS daje staly publiczny URL+TLS). Polling-runner zostaje lokalnie do dev.

---

## 4. Komponenty / pliki

| Plik | Odpowiedzialnosc | Akcja |
|------|------------------|-------|
| `src/invoicer/app.py` | Fabryka `create_app()`: buduje durable graf (Claude+Fakturownia+Twilio) + `PendingApprovals`; montuje `/whatsapp/inbound` (logika Planu B), `/health`, `/status`; lifespan startuje/zatrzymuje scheduler; init Sentry; `install_redaction`. Eksponuje `app` dla uvicorn. | Create |
| `src/invoicer/scheduler.py` | `build_scheduler(intake, *, hour, minute, tz)` (AsyncIOScheduler, cron) + `run_daily_intake(graph, channel, registry, source, detector, *, sender, phone, alert)` reuzywajacy `fetch_invoice_documents` + `request_invoice_approval`; per-faktura try/except → alert+Sentry. | Create |
| `src/invoicer/observability/sentry.py` | `init_sentry(dsn)` (ASGI integration) + `_scrub(event, hint)` (`before_send`) przepuszczajacy `redact_pii` po message/extra/exception. No-op gdy brak DSN. | Create |
| `src/invoicer/observability/status.py` | `pipeline_status(metrics, counters, registry) -> dict` (metryki LLM `totals()` + processed/failed/pending). | Create |
| `src/invoicer/observability/alerts.py` | `send_failure_alert(channel, text)` — krotka wiadomosc WhatsApp do approvera (PII tylko do wlasciciela; w logach zredagowane). | Create |
| `src/invoicer/adapters/twilio_whatsapp.py` | + metoda `notify(text: str)` (plain WhatsApp message) — reuzywana przez alerty. | Modify |
| `Dockerfile` | `python:3.12-slim` + uv; instal zaleznosci z `pyproject`/lock; `PYTHONPATH=/app/src`; CMD `uvicorn invoicer.app:app --host 0.0.0.0 --port 8080`. | Create |
| `fly.toml` | app, region (np. `waw`), `[mounts] /data`, `[http_service] internal_port=8080 auto_stop_machines=false min_machines_running=1`, healthcheck `GET /health`. | Create |
| `.github/workflows/deploy.yml` | trigger `push` na `main`; needs CI green; `superfly/flyctl-actions` `deploy`; `FLY_API_TOKEN` z sekretow GH. | Create |
| `pyproject.toml` | + `sentry-sdk`, `apscheduler`. | Modify |
| `README.md` | sekcja Deploy: `fly launch`/`fly volumes create`/`fly secrets set`, ustawienie webhooka w Twilio, jednorazowy `GMAIL_TOKEN_B64`. | Modify |

**Granice (isolation):** `app.py` tylko montuje i okablowuje (cienkie); logika zaciagu w `scheduler.py`; observability w osobnych modulach `observability/*`. Kazdy testowalny niezaleznie (stuby/TestClient).

---

## 5. Sekrety + Gmail (headless OAuth — gotcha)

- Wszystkie sekrety w **`fly secrets set`** (szyfrowane, wstrzykiwane jako env): `ANTHROPIC_API_KEY`, `TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN`, `TWILIO_WHATSAPP_FROM`, `APPROVER_WHATSAPP_TO`, `FAKTUROWNIA_API_TOKEN`, `FAKTUROWNIA_DOMAIN`, `GMAIL_SENDER_FILTER`, `SENTRY_DSN`, `INVOICER_SINK=fakturownia`. `.env` zostaje **tylko** do dev.
- **Gmail OAuth jest interaktywny** — niemozliwy headless w kontenerze. `token.json` (z refresh-tokenem) generujemy lokalnie (juz mamy), kodujemy base64 i wstrzykujemy jako sekret `GMAIL_TOKEN_B64`. Przy starcie (lifespan) dekodujemy do `/data/token.json` (jesli nie istnieje); `GMAIL_TOKEN=/data/token.json`. Biblioteka odswieza access-token refresh-tokenem.
- Konfiguracja runtime przez env: godzina zaciagu (`INTAKE_HOUR`, `INTAKE_MINUTE`, default 08:00), `INTAKE_TZ=Europe/Warsaw`.

---

## 6. Przepływ danych

```
START (lifespan): GMAIL_TOKEN_B64 → /data/token.json ; init Sentry ; install_redaction ;
                  build durable graf (SqliteSaver na /data) + registry ; start APScheduler.

CODZIENNIE 08:00 Europe/Warsaw (scheduler):
  run_daily_intake: fetch_invoice_documents(GmailAdapter, ClaudeInvoiceDetector, sender)
    dla kazdej faktury: request_invoice_approval(graf, Twilio, registry, doc, thread_id, phone)
      → start_document → payload (bramka) → registry.add + WhatsApp request
    per-faktura blad → send_failure_alert + Sentry, leci dalej.

ODPOWIEDZ TAK/NIE: Twilio → POST /whatsapp/inbound → parse_decision → registry.resolve_oldest
  → resume_document → book (FakturowniaSink). Blad resume/book → 2xx + Sentry + alert.

MONITORING: logi JSON→stdout→Fly logs ; GET /health (Fly liveness) ;
            GET /status (metryki LLM koszt/latencja + processed/failed/pending) ; Sentry (errors+alerty).
```

---

## 7. Obsługa błędów

- **Webhook:** zostaje 2xx przy porazce resume (brak retry-storm Twilio, log z `redact_pii`) — dokladamy `sentry_sdk.capture_exception` + `send_failure_alert`.
- **Zaciag:** per-faktura `try/except` (jedna zla faktura nie blokuje pozostalych) → alert + Sentry; job nie wywala schedulera.
- **Scheduler:** `max_instances=1` + `coalesce=True` + `misfire_grace_time` (brak rownoleglych zaciagow; pominiete odpalenie nie kumuluje sie).
- **Restart maszyny:** stan na wolumenie (`/data`) przezywa redeploy/restart; pending dokona sie przy nastepnej odpowiedzi. Healthcheck `/health` padajacy → Fly restartuje maszyne.
- **PII:** Sentry `before_send` przepuszcza event przez `redact_pii` (NIP/IBAN/konto/e-mail/VAT-ID) — nazwy/kwoty nie wyciekaja do Sentry. Tokeny/sekrety nigdy nie logowane.

---

## 8. Observability — szczegoly

- **Logi:** strukturalne (JSON) na stdout; Fly zbiera i przeszukuje. `install_redaction` na loggerze `invoicer` (PII redagowane u zrodla). Logger `invoicer.metrics` niesie koszt/latencje (juz jest).
- **Sentry:** wyjatki ze stack-trace + kontekst, alerty mailowe; `before_send` = redakcja PII; no-op gdy brak `SENTRY_DSN` (dev). Nowa dep `sentry-sdk`.
- **/health:** szybkie 200 (liveness; ew. ping SQLite). Uzywane przez Fly do restartu.
- **/status:** `{ llm: metrics.totals(), pipeline: {processed, failed, pending} }`. `pending` z `PendingApprovals` (count PENDING). Liczniki `processed/failed` in-memory (reset przy restarcie — OK dla podgladu biezacego; trwala historia = follow-up).
- **Alert o porazce:** `send_failure_alert(channel, "⚠️ Faktura {num}: {powod}")` na WhatsApp approvera (ten sam kanal co approve; PII trafia tylko do wlasciciela). Wyzwalany w zaciagu i w webhooku przy bledzie.

---

## 9. Testy

- `app.py`: TestClient — `GET /health` → 200; `GET /status` → klucze `llm`/`pipeline` z poprawnymi typami; `POST /whatsapp/inbound` (fake resume/registry) → 'resumed'/'no_pending'/'ignored' (reuzycie logiki Planu B, ktora juz ma testy).
- `scheduler.py`: `run_daily_intake` na stubach (StubEmailSource/StubDetector/StubApprovalChannel + fake registry) → woła request_approval per wykryta faktura; symulowany wyjatek jednej faktury → `send_failure_alert` zawolany, reszta przetworzona.
- `observability/sentry.py`: `_scrub` na evencie z NIP/e-mail w message/extra/exception → po `before_send` brak surowego NIP/e-mail (jest `[NIP]`/`[EMAIL]`); brak DSN → init no-op.
- `observability/status.py`: `pipeline_status` agreguje metryki + liczniki + pending z fake registry.
- `observability/alerts.py` + `TwilioWhatsAppChannel.notify`: fake client → poprawny POST (From/To/Body), blad → wyjatek z PII zredagowanym.
- **Live/manual (udokumentowane, nie CI):** smoke deploy na Fly (fly deploy → /health 200 → testowy zaciag → realny TAK → ksiegowanie). Sekrety realne.
- Istniejacy suite (205/7) zielony; nowe deps nie psuja importow.

---

## 10. Ryzyka / decyzje

- **Always-on (nie scale-to-zero):** webhook + scheduler musza zyc → `auto_stop_machines=false`, `min_machines_running=1`. Koszt: jedna mala maszyna 24/7 (~kilka $/mc). Swiadome.
- **Scheduler in-process (APScheduler) vs cron PaaS:** in-process = jeden artefakt/deploy/baza; dla skali (1 user, kilka fv/dzien) wystarcza. Job sync (Claude/Gmail/Twilio blokujace) → uruchamiany w executorze, by nie blokowac petli asyncio.
- **Webhook zamiast pollingu:** staly URL `*.fly.dev` rozwiazuje to, co ngrok mial rozwiazac; URL wkleja uzytkownik w konsoli Twilio (jednorazowo). Podpis `X-Twilio-Signature` — nadal opcjonalny (MVP), endpoint wznawia tylko istniejace pending; weryfikacja podpisu = mozliwy follow-up.
- **Gmail token headless:** brak interaktywnego OAuth w kontenerze → `GMAIL_TOKEN_B64` → `/data/token.json`. Refresh-token dlugozyjacy; wygasniecie = ponowna autoryzacja lokalnie + nowy sekret (nota w README).
- **Realne ksiegowanie 24/7:** `INVOICER_SINK=fakturownia` → po kazdym TAK realna faktura. Brak auto-approve nadal obowiazuje: ksiegowanie tylko po odpowiedzi czlowieka. Guard duplikatow w `book` chroni przed podwojnym ksiegowaniem.
- **Liczniki /status in-memory:** reset przy restarcie (podglad biezacy, nie historia) — swiadome YAGNI; trwala metryka = follow-up.
- **Sekrety:** wylacznie `fly secrets` (nie repo); `.env`/`token.json` gitignorowane.

---

## 11. Podział na plany (przy `writing-plans`)

- **Plan 1 — runtime 24/7 (gałąź `feat/deploy-runtime`):** `app.py` (fabryka + /health + /status + montaz webhooka), `scheduler.py` (job + APScheduler), `TwilioWhatsAppChannel.notify`, dep `apscheduler`, `Dockerfile`, `fly.toml`, sekrety + `GMAIL_TOKEN_B64`/`/data/token.json`. **Rezultat:** agent stoi 24/7 na Fly, sam zaciaga i przyjmuje akceptacje, /health+/status zyja.
- **Plan 2 — observability + CI/CD (osobna gałąź):** `observability/sentry.py` (+`before_send` PII), `observability/alerts.py`, wpiecie alertow w zaciag+webhook, dep `sentry-sdk`, `.github/workflows/deploy.yml` (auto-deploy), README Deploy. **Rezultat:** widac CO/GDZIE peklo (Sentry), alert o porazce, deploy bez reki.

Kazdy plan: subagent-driven (implementer + 2-stopniowy review per task + finalowy opus), merge `--no-ff` do `main`.
