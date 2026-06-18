# Invoicer — projekt (design spec)

- **Data:** 2026-06-18
- **Status:** Zatwierdzony kierunek; gotowy do planu implementacji
- **Autor:** Michał Studniarski (+ Claude jako architekt)
- **Repo:** osobne (`Invoicer`), niezależne od projektu Aida

---

## 1. Kontekst i cel

**Invoicer** to agentowy asystent księgowy: pobiera fakturę PDF od konkretnego
klienta (po adresie e-mail nadawcy), wyciąga z niej dane, waliduje je pod kątem
polskiego prawa podatkowego, klasyfikuje traktowanie podatkowe (w szczególności
faktury zagraniczne bez VAT, np. z UK), a po **zatwierdzeniu przez człowieka**
zapisuje dekret do programu księgowego (Subiekt).

**Charakter projektu:** portfolio / nauka — flagowy element CV pod rolę AI
Engineer. Priorytety: czysta architektura agentowa, czytelne wzorce (tool use,
structured output, human-in-the-loop, obsługa wyjątków), przekonujące demo.
Zgodność podatkowa ma być **wiarygodna**, nie produkcyjna — ostateczna decyzja
zawsze należy do człowieka (księgowego).

**Czym Invoicer NIE jest:** nie jest certyfikowanym narzędziem podatkowym ani
autonomicznym agentem księgującym bez nadzoru. Każdy zapis przechodzi przez
bramkę akceptacji człowieka.

---

## 2. Zakres (MVP)

**W zakresie:**
- Wejście: PDF w załączniku e-mail (tekstowe **i** skany/zdjęcia).
- Pobranie z **realnej** skrzynki Gmail (OAuth) z filtrem po adresie nadawcy.
- Ekstrakcja danych modelem wizyjnym Claude → ustrukturyzowany model `Invoice`.
- Walidacja (logika lokalna, bez zewnętrznych API): suma kontrolna NIP, zgodność
  sum, kompletność pól, wykrywanie duplikatów.
- Klasyfikacja traktowania podatkowego: krajowa (PL VAT) vs zagraniczna bez VAT
  (kraj trzeci, np. UK → import usług / import towarów, odwrotne obciążenie).
- Bramka **human-in-the-loop** (LangGraph `interrupt`) — akceptacja / edycja /
  odrzucenie.
- Zapis przez adapter **`AccountingSink`**: mock Subiekt (loguje gotowy
  `BookingPayload`), realny `SubiektSferaSink` jako udokumentowany szkielet.
- Zestaw fixture'ów + evaluacje uruchamiane w CI.

**Poza zakresem (świadome YAGNI, szwy zostawione):**
- Biała Lista podatników VAT (MF), VIES, kursy NBP — architektura zostawia je
  jako kolejne "narzędzia walidacyjne" do dodania.
- Realny zapis do Subiekta (wymaga Windows + COM/OLE; deweloper pracuje na macOS).
- KSeF / strukturalny XML FA(2) — możliwe rozszerzenie wejścia.
- Pełna obsługa import towarów (odprawa celna/SAD) — w MVP wykrywany, ale
  kierowany do człowieka jako ogólna flaga.
- Obsługa wielu nadawców równolegle / kolejkowanie skali produkcyjnej.

---

## 3. Architektura: porty i adaptery wokół rdzenia LangGraph

Rdzeń (graf stanów) nie zna szczegółów Gmaila ani Subiekta — komunikuje się przez
interfejsy (porty). Dzięki temu I/O jest wymienne bez dotykania logiki agenta.

| Port            | Adapter (demo)                 | Adapter (realny / przyszły)        |
|-----------------|--------------------------------|------------------------------------|
| `EmailSource`   | `GmailAdapter` (realny, OAuth) + `FixtureSource` (PDF lokalne) | — |
| `AccountingSink`| `MockSubiektSink` (loguje payload) | `SubiektSferaSink` (Windows/COM, szkielet) |
| `HumanReview`   | `CliReview` (Rich)             | `StreamlitReview` (opcjonalnie)    |

**LLM:** Claude (vision) przez `langchain-anthropic` — ekstrakcja ze skanów i
węzeł rozumowania `reason_exception`. Domyślny model: Claude Sonnet (vision),
z możliwością podniesienia do Opus dla trudnych skanów.

---

## 4. Graf stanów (LangGraph)

```
fetch_email → extract → validate → classify → [reason_exception?] → human_review → book → end
```

### Węzły

1. **`fetch_email`** — pobiera wiadomość od wskazanego nadawcy przez
   `EmailSource`, wyciąga załączniki PDF.
2. **`extract`** — Claude vision → `Invoice` (structured output / Pydantic):
   sprzedawca (nazwa, NIP, kraj), nabywca, nr faktury, daty, pozycje, netto/VAT/
   brutto wg stawek, waluta, płatność. Niska pewność / brak pola → flaga.
3. **`validate`** — kontrole deterministyczne (czyste funkcje): suma kontrolna
   NIP, `netto+VAT=brutto` oraz Σ pozycji, kompletność pól, wykrycie duplikatu
   (nr faktury + NIP sprzedawcy względem lokalnego `ledger`), sensowność dat.
4. **`classify`** — routing podatkowy (krawędź warunkowa):
   - sprzedawca z **PL** (polski NIP, VAT wykazany) → faktura **krajowa** →
     prosto do `human_review`;
   - sprzedawca **zagraniczny / brak VAT** → `reason_exception`.
5. **`reason_exception`** — Claude generuje ustrukturyzowaną `Classification`:
   proponowane traktowanie + uzasadnienie (PL) + pewność + lista rzeczy do
   potwierdzenia przez człowieka + nota walutowa. (szczegóły w sek. 6)
6. **`human_review`** — **`interrupt()`**: graf zatrzymuje się, checkpoint
   utrwala stan. Człowiek widzi podsumowanie ekstrakcji, flagi walidacji i (dla
   wyjątków) rozumowanie. Akcje: **zatwierdź / edytuj / odrzuć**.
7. **`book`** — po zatwierdzeniu mapuje `Invoice (+ decyzja człowieka)` na
   `BookingPayload` i woła `AccountingSink.post()`. Dopisuje do `ledger`
   (duplikaty + audyt).
8. **`end`** — emituje `AuditRecord`.

### Krawędzie warunkowe

- `validate` → twarde błędy (zła suma kontrolna NIP, niespójne sumy, brak pola)
  **nie blokują cicho** — lecą jako flagi do `human_review`.
- `classify` → `reason_exception` (zagraniczna) albo `human_review` (krajowa).
- `human_review` → `book` (zatwierdź) | `end` (odrzuć) | `extract`/`validate`
  (popraw / ponów).

### Stan grafu (`InvoiceState`)

Pola: referencja do maila, ścieżki/bajty PDF, `Invoice` (ekstrakcja),
`ValidationResult`, `Classification`, decyzja człowieka, `BookingResult`, lista
flag/błędów, `AuditRecord` (ślad audytowy). Persystencja przez checkpointer
LangGraph (SQLite na start).

---

## 5. Modele danych (Pydantic v2)

- **`Party`** — `name`, `nip` (opcjonalny dla zagranicznych), `country` (ISO),
  `address`, `vat_id` (np. GB...).
- **`LineItem`** — `description`, `quantity`, `unit_net`, `vat_rate`, `net`,
  `vat`, `gross`.
- **`Invoice`** — `seller: Party`, `buyer: Party`, `number`, `issue_date`,
  `sale_date`, `due_date`, `currency`, `lines: list[LineItem]`, sumy wg stawek,
  `total_net`, `total_vat`, `total_gross`, `extraction_confidence`.
- **`ValidationResult`** — `checks: list[Check]` (nazwa, status pass/warn/fail,
  szczegół), `is_duplicate`, `hard_errors`, `soft_flags`.
- **`Classification`** — `treatment` (enum: `krajowa` | `import_uslug` |
  `import_towarow` | `wnt` | `inne`), `country_bucket` (`PL`|`UE`|`poza_UE`),
  `confidence`, `rationale_pl`, `human_must_confirm: list[str]`, `currency_note`.
- **`BookingPayload`** — znormalizowany dekret dla `AccountingSink` (kontrahent,
  pozycje, stawki/oznaczenia, traktowanie, waluta, kwoty).
- **`AuditRecord`** — co wyciągnięto, jakie flagi, czyja/jaka decyzja, znaczniki
  czasu, wersja modelu.

---

## 6. Walidacja i klasyfikacja podatkowa (serce "polskiego prawa")

### Walidacja (lokalna, czyste funkcje — łatwe w TDD)

- **Suma kontrolna NIP** — algorytm wagowy (wagi 6,5,7,2,3,4,5,6,7 mod 11).
- **Zgodność sum** — `netto+VAT=brutto` per pozycja i globalnie; Σ pozycji =
  sumy faktury (tolerancja groszowa na zaokrąglenia).
- **Kompletność pól** — wymagane: nr, daty, sprzedawca, kwoty.
- **Duplikat** — `(numer, NIP sprzedawcy)` względem lokalnego `ledger`.

### Klasyfikacja (kontrast, który agent ma rozpoznać)

| Cecha                | Faktura krajowa (PL)        | Faktura spoza UE (np. UK)                       |
|----------------------|-----------------------------|-------------------------------------------------|
| Sprzedawca           | polski NIP                  | brak PL NIP, kraj trzeci (np. GB)               |
| VAT na dokumencie    | wykazany (23/8/5/0/zw)      | **brak VAT**                                    |
| Traktowanie          | standardowe księgowanie     | **odwrotne obciążenie** (nabywca rozlicza VAT)  |
| Usługa               | —                           | **import usług** (miejsce świadczenia w PL)     |
| Towar                | —                           | **import towarów** (odprawa celna — inna ścieżka)|
| Waluta               | zwykle PLN                  | zwykle obca (GBP) → kurs NBP do potwierdzenia   |

`reason_exception` (LLM) dla faktury zagranicznej bez VAT:
- **proponuje** najbardziej prawdopodobne traktowanie — domyślnie *import usług*
  (najczęstsze dla UK: SaaS, usługi zdalne),
- podaje `rationale_pl` (krótkie uzasadnienie po polsku) i `confidence`,
- wypełnia `human_must_confirm`, np.: „usługa czy towar?", „stawka do
  samonaliczenia (zwykle 23%)", „kurs waluty (NBP z dnia poprzedzającego)",
- ustawia `currency_note` przy walucie ≠ PLN.

> Wszystkie traktowania są **propozycjami do potwierdzenia przez człowieka** —
> Invoicer nie podejmuje wiążących decyzji podatkowych.

---

## 7. Human-in-the-loop, błędy, idempotencja

- **Twarda reguła:** żaden zapis bez akceptacji człowieka — bramka
  `human_review` to **jedyne** wejście do `book`.
- **Retry z backoffem** na wywołaniach LLM i Gmail; ponowienie przy niezgodności
  structured-output (walidacja Pydantic wymusza ponowną próbę).
- **Niska pewność / brak pola** → flaga, nigdy auto-book.
- **Idempotencja:** wykrywanie duplikatów + checkpoint LangGraph (brak podwójnej
  księgowości przy wznowieniu po przerwie).
- **Ślad audytowy:** pełny `AuditRecord` w stanie (ekstrakcja, flagi, decyzja).

---

## 8. Obserwowalność i evaluacje (motyw „evals-as-CI")

- Opcjonalny tracing LangSmith.
- **Zestaw fixture'ów** (PDF + oczekiwany JSON): typowa PL · **UK bez VAT** ·
  słaby skan · brak pola · duplikat. Przepuszczane przez graf w CI z asercjami na
  ekstrakcję i klasyfikację. Claude może być nagrany/odtwarzany lub wołany realnie
  za flagą.

---

## 9. Stack i układ repo

**Stack:** Python 3.12 · **uv** · LangGraph + `langchain-anthropic` (Claude
vision) · Pydantic v2 · pytest · ruff · `google-api-python-client` (Gmail) ·
Rich (CLI HITL); opcjonalnie Streamlit.

```
Invoicer/
  pyproject.toml
  README.md
  src/invoicer/
    graph.py              # definicja grafu LangGraph
    state.py              # InvoiceState
    models.py             # Invoice, LineItem, Party, Classification, ...
    validation.py         # suma kontrolna NIP, sumy, duplikaty
    ports.py              # Protokoły: EmailSource, AccountingSink, HumanReview
    ledger.py             # lokalny store (duplikaty + audyt)
    config.py
    llm.py                # konfiguracja ChatAnthropic
    nodes/                # fetch_email, extract, validate, classify,
                          # reason_exception, human_review, book
    adapters/             # gmail, fixture_source, mock_subiekt, cli_review
  tests/
    unit/                 # NIP, sumy, duplikaty, mappery (TDD)
    fixtures/             # przykładowe PDF + oczekiwany JSON
    evals/                # przejścia end-to-end przez graf
  docs/superpowers/specs/2026-06-18-invoicer-design.md
```

---

## 10. Kamienie milowe (propozycja podziału na plan)

1. **Szkielet + modele** — repo, `uv`, Pydantic `Invoice`/`Party`/`LineItem`,
   `validation.py` (TDD: NIP, sumy, duplikaty).
2. **Porty + adaptery mock** — `EmailSource`/`FixtureSource`,
   `AccountingSink`/`MockSubiektSink`, `ledger`.
3. **Graf bazowy** — węzły `extract` (Claude vision) → `validate` → `classify`
   → `human_review` (CLI) → `book`, na fixture'ach.
4. **Wyjątek zagraniczny** — `reason_exception` (UK bez VAT → import usług),
   `Classification`, ścieżka HITL z uzasadnieniem.
5. **Realny Gmail** — `GmailAdapter` (OAuth, filtr nadawcy).
6. **Evals w CI** — zestaw fixture'ów + asercje, ruff, pytest w GitHub Actions.

**Szwy na przyszłość:** Biała Lista MF / VIES / NBP jako kolejne narzędzia
walidacyjne; `SubiektSferaSink` (Windows/COM); wejście KSeF XML; UI Streamlit.

---

## 11. Pytania otwarte

- Czy w demo `human_review` ma od razu mieć wariant Streamlit, czy wystarczy CLI?
- Czy nagrywać odpowiedzi Claude do evalów (deterministyczne CI), czy wołać
  realnie za flagą?
