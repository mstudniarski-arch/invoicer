# Bramka compliance (biała lista VAT + VIES) — design

- Data: 2026-07-01
- Status: zatwierdzony (design), przed planem implementacji
- Zakres: pre-book compliance-gate w czystym Pythonie, wpięty w istniejący graf LangGraph

## 1. Cel i wartość

Dodaje brakujący etap **verify** między `extract` a `book` — z „wyciąga i księguje" robi realny proces AP (accounts payable). Co daje:

1. **Ochrona podatkowa.** Zapłata na konto spoza białej listy przy transakcji **> 15 000 zł** = utrata kosztu (KUP) + solidarna odpowiedzialność za VAT dostawcy (art. 117ba Ord. pod., 15d CIT / 22p PIT). Bramka wyłapuje to przed księgowaniem/płatnością.
2. **Dowód należytej staranności.** API MF zwraca `requestId` — prawny dowód sprawdzenia kontrahenta w danym dniu. Zapisywany w ledgerze (audyt).
3. **Wyłapanie martwych dostawców.** Status VAT `Czynny` — wyrejestrowany/zwolniony dostawca zagraża odliczeniu VAT.
4. **VIES dla UE.** Weryfikuje ważność numeru VAT-UE kontrahenta — bez tego reverse-charge/WNT nie ma podstaw.
5. **Fundament pod płatności** (kolejny etap): płatność tylko na zweryfikowane konto.

Filozofia bez zmian: wynik trafia do bramki człowieka (miękka bramka), nie wprowadza nowej autonomii ani nowej infrastruktury. Oba API są darmowe i bez kluczy.

## 2. Zakres

**MVP** (bez zmian w ekstrakcji):
- Status VAT po NIP (PL) — `Czynny` / `Zwolniony` / `Niezarejestrowany`.
- Pobranie zarejestrowanych rachunków dostawcy po NIP (do późniejszego użytku przy płatnościach).
- VIES dla dostawców UE — ważność numeru VAT-UE.
- Wynik jako flagi w payloadzie `human_review` + `request_id` w `LedgerEntry`.
- Miękka bramka (flagi do człowieka), z opcjonalnym twardym blokiem via konfiguracja.
- Odporność: błąd/timeout API → wynik `unknown` + flaga, nigdy crash intake.

**Faza 2** (mała zmiana modelu):
- Dodanie `seller_bank_account` do ekstrakcji → pełny check „to konkretne konto ⊆ biała lista".
- Opcjonalna sugestia ZAW-NR gdy konto spoza listy a kwota > 15k.

**Non-goals:**
- Nie jest to substytut doradcy podatkowego; wszystko nadal gatuje człowiek.
- Brak automatycznego składania ZAW-NR (tylko sugestia).
- POZA_UE: brak publicznego rejestru odpowiadającego białej liście/VIES → check pomijany (z jawną adnotacją).

## 3. Architektura (ports-and-adapters, jak reszta projektu)

### Port
Nowy `ComplianceChecker` (Protocol w `src/invoicer/ports.py`):

```
check(invoice: Invoice, bucket: CountryBucket) -> ComplianceResult
```

### Adaptery
- `StubComplianceChecker` — CI/offline, zwraca kanoniczny wynik, zero sieci.
- `PlComplianceChecker` — realny; routuje po `country_bucket`:
  - `PL` → biała lista MF (`wl-api.mf.gov.pl`): status VAT + lista kont po NIP.
  - `UE` → VIES REST (`ec.europa.eu/.../vies/rest-api`): ważność VAT-UE.
  - `POZA_UE` → skip (wynik `not_applicable` + adnotacja).
  - Klient HTTP (`httpx`) wstrzykiwany — testowalny fake'iem jak `FakturowniaSink`.

Dokładne endpointy i pola odpowiedzi zostaną potwierdzone w planie implementacji i live-teście (patrz §8).

### Model danych
`ComplianceResult` (Pydantic, w `src/invoicer/models.py`):

- `vat_status`: enum `czynny | zwolniony | niezarejestrowany | unknown | not_applicable`
- `account_whitelisted`: enum `tak | nie | nieznane | not_applicable` (MVP: zwykle `nieznane`, bo brak nr konta)
- `registered_accounts`: `list[str]` (konta zwrócone przez MF; do przyszłej płatności)
- `vies_valid`: enum `tak | nie | not_applicable | unknown`
- `flags`: `list[str]` (czytelne komunikaty dla człowieka)
- `request_id`: `str | None` (dowód sprawdzenia MF)
- `checked_at`: `str` (ISO, ustawiane przez wołającego — determinizm w testach)

### Węzeł i wpięcie w graf
- `make_compliance_node(checker)` w `src/invoicer/graph/nodes.py`: czyta `invoice` + `classification.country_bucket`, woła `checker.check(...)`, zapisuje `compliance` do stanu.
- Stan: dodać `compliance: ComplianceResult | None` do `InvoiceState`.
- Wpięcie: **po `classify`, przed `human_review`**, na obu ścieżkach:
  - PL: `classify → compliance_check → human_review`
  - foreign: `verify_grounding → compliance_check → human_review`
- `human_review` czyta `state["compliance"]` i dokłada `flags` do payloadu interruptu oraz do `human_must_confirm`.
- `book`: przenosi `compliance.request_id` do `LedgerEntry` jako provenance (nowe, opcjonalne pole poza rdzeniem hasha — zgodnie z istniejącym wzorcem prev_hash/entry_hash).

## 4. Zachowanie per bucket

| bucket | check | źródło |
|---|---|---|
| PL | status VAT + rejestrowane konta | biała lista MF |
| UE | ważność VAT-UE | VIES |
| POZA_UE | brak (adnotacja) | — |

Mapuje się 1:1 na istniejący `country_bucket` z węzła `classify`.

## 5. Bramka i odporność

- **Miękka bramka (domyślnie):** flagi + `human_must_confirm` w payloadzie; człowiek widzi status i decyduje. Zero blokowania.
- **Opcjonalny twardy blok** (`COMPLIANCE_HARD_BLOCK=1`): `vat_status == niezarejestrowany` traktuj jak `validate` FAIL → routing do END, bez pytania. (Router `route_after_compliance` albo rozszerzenie istniejącego — do rozstrzygnięcia w planie; domyślnie węzeł nie rozgałęzia, tylko flaguje.)
- **Degradacja:** timeout/błąd/HTTP != 2xx → `vat_status/vies_valid = unknown` + flaga „nie udało się zweryfikować — sprawdź ręcznie". Nigdy nie wywala intake (wzorzec best-effort jak `mark_read`). Krótki timeout, brak retry-storm.
- **Prywatność/logi:** do zapytań idzie tylko NIP/VAT-UE (dane publiczne), bez PII nabywcy. Odpowiedzi z ewentualnymi danymi wrażliwymi redagowane w logach (istniejący `security.py`).

## 6. Konfiguracja (env)

- `COMPLIANCE_ENABLED` (domyślnie `true` w prod, `false`/stub w CI) — wpina real vs stub w `build_*`.
- `COMPLIANCE_HARD_BLOCK` (domyślnie `false`).
- `COMPLIANCE_HTTP_TIMEOUT_S` (domyślnie np. 8).
- Brak kluczy API (MF i VIES są publiczne/darmowe).

## 7. Zależność: numer konta dostawcy

Pełny check „to konto jest na białej liście" wymaga IBAN/NRB dostawcy, którego dziś nie ekstrahujemy. MVP działa bez tego (status VAT + lista kont + VIES) i daje `request_id`. Faza 2: `seller_bank_account` w `InvoiceExtraction` → pełny check + gotowość pod płatności.

## 8. Testy

- **Unit (CI, offline):**
  - `StubComplianceChecker` + test węzła: flagi trafiają do payloadu `human_review`; `request_id` ląduje w `LedgerEntry`.
  - `PlComplianceChecker` z fake'owym `httpx` (jak testy Fakturowni): asercje wybranych endpointów, parsowania statusu/kont/VIES, oraz ścieżki degradacji (błąd → `unknown` + flaga, bez wyjątku).
  - Routing per bucket (PL/UE/POZA_UE) + zachowanie przy `COMPLIANCE_HARD_BLOCK`.
- **Live-gated (sieć):** realne MF/VIES; oznaczone jako sieciowe/wolne, żeby CI nie było flaky. Tu potwierdzamy dokładne kształty odpowiedzi.

## 9. Fazy

1. **MVP:** port + `ComplianceResult` + stub + `PlComplianceChecker` (VAT status + rejestrowane konta + VIES) + węzeł + flagi w bramce + `request_id` w ledgerze + degradacja + konfiguracja. Miękka bramka.
2. **Rozszerzenie:** `seller_bank_account` w ekstrakcji → pełny check białej listy; opcjonalny twardy blok; sugestia ZAW-NR.

## 10. Ryzyka / ograniczenia

- Limity zapytań MF (dzienne / bulk) — pojedyncze checki OK; przy większym wolumenie rozważyć cache po NIP+dacie.
- Kształt odpowiedzi API bywa zmieniany przez MF/KE — kontrakt zabezpieczamy live-testem i defensywnym parsowaniem (brakujące pola → `unknown`, nie wyjątek).
- Narzędzie wspiera należytą staranność, nie zastępuje doradcy — decyzja zawsze u człowieka.
