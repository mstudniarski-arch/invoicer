# Invoicer — Design: dwukierunkowy approve faktur przez WhatsApp (Twilio)

**Data:** 2026-06-23
**Status:** zatwierdzony projekt (realizacja subagent-driven, w **2 planach** — patrz §6)
**Realizuje:** człowiek dostaje request akceptacji na WhatsApp (sprzedawca / NIP / kwota) i zatwierdza/odrzuca odpowiedzią „TAK/NIE", co wznawia graf i księguje.

---

## 1. Problem / kontekst

Graf pauzuje na węźle `human_review` (`interrupt`, stan w checkpointerze pod `thread_id`); `resume_document(thread_id, decision)` wznawia. Dziś akceptacja jest synchroniczna (Streamlit/CLI, `InMemorySaver` — stan ginie po procesie). Cel: **asynchroniczny HITL przez WhatsApp** — request idzie na telefon, a odpowiedź (później, z innego procesu) wznawia graf.

**Dwie konsekwencje dla architektury:**
1. **Trwały stan.** Luka między wysłaniem requestu a odpowiedzią (minuty/godziny, inny proces) wymaga **durable checkpointera** (`SqliteSaver`) zamiast `InMemorySaver`. `build_invoice_graph(checkpointer=...)` już przyjmuje param.
2. **Payload `human_review` nie ma NIP-u.** Dziś zawiera `number/seller/country/total_gross/currency/validation_ok/flags/treatment/rationale/must_confirm` ([nodes.py](../../../src/invoicer/graph/nodes.py)). Użytkownik chce NIP → dodać `seller_nip` do payloadu (drobna zmiana węzła).

**Wzorce w repo:** porty + adaptery (`AccountingSink`/`EmailSource`/`InvoiceDetector`), wstrzykiwany klient HTTP (Fakturownia/Gmail), live-gated testy, stub do CI.

---

## 2. Zakres

**W zakresie (całość, w 2 planach):**
- Durable checkpointer (`SqliteSaver`) — resume po „restarcie".
- `seller_nip` w payloadzie `human_review`.
- Port `ApprovalChannel` + `TwilioWhatsAppChannel` (wychodzące) + `StubApprovalChannel` (CI).
- Rejestr `PendingApprovals` (mapowanie numer telefonu → `thread_id`).
- Webhook `FastAPI` (`POST /whatsapp/inbound`) — parsuje TAK/NIE, wznawia właściwy thread, księguje.
- Orkiestracja: faktura → `start_document` (durable) → `request_approval` → [webhook] → `resume_document` → book.
- Config (env Twilio) + zależności; testy jednostkowe + live-gated.

**Poza zakresem (świadome YAGNI):**
- Approve poza kolejnością przy wielu pending naraz (MVP: FIFO per numer; ref-w-wiadomości to follow-up).
- Bogate szablony/multimedia WhatsApp, statusy „delivered/read".
- Multi-tenant / wielu approverów (jeden numer approvera w MVP).
- Pełna automatyzacja live e2e (realna odpowiedź → webhook wymaga Twilio + publicznego URL/ngrok — setup użytkownika, jak Gmail OAuth).
- Streamlit/mock i reszta grafu — nietknięte (poza `seller_nip` w payloadzie + wstrzyknięciem checkpointera).

---

## 3. Architektura

### 3.1 Durable checkpointer (Plan A)
`SqliteSaver` (pakiet `langgraph-checkpoint-sqlite`) wstrzykiwany do `build_invoice_graph(checkpointer=saver)`. Helper `build_persistent_graph(*, db_path, ...)` lub przekazanie savera w orkiestracji. Kluczowa własność: **graf zbudowany z tej samej bazy w nowym procesie wznawia istniejący `thread_id`** (`resume_document`).

### 3.2 `seller_nip` w payloadzie (Plan A)
W `human_review` ([nodes.py](../../../src/invoicer/graph/nodes.py)) dodać `"seller_nip": invoice.seller.nip` do dict-a `interrupt(payload)`. Istniejący Streamlit ignoruje nadmiarowy klucz (bezpieczne).

### 3.3 Port `ApprovalChannel` + adaptery (Plan A)
```python
@runtime_checkable
class ApprovalChannel(Protocol):
    def request_approval(self, payload: dict, *, thread_id: str) -> None: ...
```
- `TwilioWhatsAppChannel(client, *, account_sid, auth_token, from_whatsapp, to_whatsapp)`: formatuje wiadomość (sprzedawca/NIP/kwota + instrukcja TAK/NIE) i `POST https://api.twilio.com/2010-04-01/Accounts/{sid}/Messages.json` (basic auth, form: `From`/`To`/`Body`). Wstrzykiwany klient HTTP (CI: fake; live: httpx). Błędy → wyjątek z body przez `redact_pii`.
- `StubApprovalChannel`: rejestruje wywołania (do CI/orkiestracji offline).
- Format wiadomości (z payloadu): `🧾 Faktura {number}\nOd: {seller}\nNIP: {seller_nip}\nKwota: {total_gross} {currency}\nTraktowanie: {treatment}\nOdpowiedz TAK (zatwierdź) lub NIE (odrzuć).`

### 3.4 Rejestr `PendingApprovals` (Plan B)
Trwały store (tabela w bazie SQLite): `(thread_id, sender_phone, status, created_at)`. API: `add(thread_id, phone)`, `resolve_oldest(phone) -> thread_id | None` (najstarszy `PENDING` dla numeru → oznacz `RESOLVED`). Testowalny na temp-bazie.

### 3.5 Webhook `FastAPI` (Plan B)
`POST /whatsapp/inbound` (Twilio woła, `application/x-www-form-urlencoded`: `From`, `Body`). Logika: `phone = From`; `decision = parse(Body)` (`TAK/yes/approve/1 → "approve"`, `NIE/no/reject/2 → "reject"`, inaczej → ignoruj/poproś o TAK/NIE); `thread_id = registry.resolve_oldest(phone)`; `resume_document(graph, thread_id, decision)`; (opcjonalnie) odeślij potwierdzenie. `graph` budowany z tej samej bazy SQLite (durable). Walidacja podpisu Twilio — opcjonalna (nota).

### 3.6 Orkiestracja (Plan B)
`request_invoice_approval(graph, channel, registry, document, *, thread_id, phone)`: `start_document` (durable) → jeśli payload (pauza na human_review) → `registry.add(thread_id, phone)` + `channel.request_approval(payload, thread_id=thread_id)`. Webhook domyka pętlę.

---

## 4. Przepływ danych

```
faktura → start_document (graf z SqliteSaver) → human_review (PAUZA, stan w SQLite)
        → registry.add(thread_id, phone) ; channel.request_approval(payload)
              WhatsApp: "🧾 Faktura FV/123 · Od: ACME · NIP: 5260001246 · 1230,00 PLN · TAK/NIE"
        → [człowiek] "TAK"  → Twilio → POST /whatsapp/inbound
        → thread_id = registry.resolve_oldest(phone) ; decision = "approve"
        → resume_document(graph, thread_id, "approve") → book → potwierdzenie na WhatsApp
```

---

## 5. Testy

**Plan A:**
- `SqliteSaver`: `start_document` na grafie z bazą → nowy obiekt grafu z TEJ SAMEJ bazy → `resume_document` wznawia (dowód trwałości).
- `seller_nip` w payloadzie `human_review` (rozszerzenie testu grafu/nodes).
- `TwilioWhatsAppChannel.request_approval` (fake client): poprawny URL/auth/`To`/`From`/`Body` zawiera sprzedawcę, **NIP**, kwotę; błąd → wyjątek z PII zredagowanym; zgodność z portem `ApprovalChannel`.
- `StubApprovalChannel`: rejestruje wywołania.
- Live-gated: realny Twilio (sandbox) wysyła wiadomość (skip bez creds).

**Plan B:**
- `PendingApprovals`: add/resolve_oldest (FIFO), temp SQLite.
- Webhook (`fastapi.testclient.TestClient`): `POST /whatsapp/inbound` z `Body=TAK` → woła `resume_document(thread_id, "approve")` dla właściwego threada (fake resume/registry); `Body=NIE` → reject; nierozpoznane → brak resume; brak pending → bezpieczna odpowiedź.
- Orkiestracja: `request_invoice_approval` rejestruje pending + woła `request_approval` (stub).
- E2E na żywo (Twilio + ngrok + serwer) — manualne, udokumentowane (nie CI).

---

## 6. Podział na plany

- **Plan A — wychodzące + trwałość (ten branch `feat/whatsapp-approval-a`):** `langgraph-checkpoint-sqlite` dep; `seller_nip` w payloadzie; port `ApprovalChannel`; `TwilioWhatsAppChannel`; `StubApprovalChannel`; durable-resume test; env Twilio. **Rezultat:** request z sprzedawca/NIP/kwota leci na WhatsApp; resume działa po „restarcie".
- **Plan B — przychodzące + orkiestracja (osobny branch):** `fastapi`/`uvicorn` dep; `PendingApprovals`; webhook `/whatsapp/inbound`; `request_invoice_approval`; testy webhooka. **Rezultat:** odpowiedź TAK/NIE wznawia graf i księguje.

Każdy plan: subagent-driven (implementer + 2-stopniowy review per task + finałowy opus), merge `--no-ff` do `main`.

---

## 7. Ryzyka / decyzje

- **Durable checkpointer:** `SqliteSaver` — konieczny dla async (stan przeżywa proces). `build_invoice_graph` już ma param `checkpointer`.
- **Mapowanie odpowiedź→thread:** rejestr per numer telefonu, FIFO (najstarszy pending). Wystarczające dla jednego approvera/sekwencyjnie; out-of-order = follow-up (ref w wiadomości).
- **Twilio za portem:** `ApprovalChannel` + stub → CI deterministyczne, zmiana dostawcy = nowy adapter. `auth_token`/`account_sid` nigdy w logach; błędy przez `redact_pii`.
- **NIP w payloadzie:** mała zmiana węzła; Streamlit ignoruje nadmiarowy klucz.
- **Webhook bezpieczeństwo:** walidacja podpisu Twilio (`X-Twilio-Signature`) — zalecane; w MVP opcjonalne (nota), bo endpoint i tak tylko wznawia istniejące pending.
- **Live e2e:** wymaga Twilio sandbox + publicznego URL (ngrok) + uruchomionego serwera — setup użytkownika; kod w pełni unit-testowalny (fake client, TestClient, temp SQLite).
- **Granice:** reszta grafu/Streamlit/Fakturownia nietknięte (poza `seller_nip` + wstrzyknięciem checkpointera).
