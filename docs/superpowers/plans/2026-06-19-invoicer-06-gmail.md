# Invoicer — Plan 06: Gmail Connector Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Dodać realny `GmailAdapter` (`EmailSource`) — pobiera załączniki-faktury PDF od konkretnego nadawcy z realnej skrzynki Gmail (OAuth, scope **read-only**), domykając oryginalny wymóg „pobranie faktury po adresie e-mail".

**Architecture:** Ten sam wzorzec testowalności co `ClaudeVisionExtractor`: `GmailAdapter` przyjmuje **wstrzykiwany `service`** (zasób Gmail API), więc logika `fetch()` jest w 100% testowalna w CI fake-service'em. Parsowanie wiadomości (query, nagłówki, rekurencyjne wyłuskanie części PDF, dekodowanie base64url) to czyste funkcje. Realny OAuth (`InstalledAppFlow.run_local_server`) i budowa serwisu są w osobnym module auth — uruchamiane raz, manualnie; pokryte testem live-gated. Scope `gmail.readonly` = least privilege (spec §9).

**Tech Stack:** Python 3.12, uv, **google-api-python-client** + **google-auth** + **google-auth-oauthlib**, Pydantic v2, pytest, ruff.

**Spec:** `docs/superpowers/specs/2026-06-18-invoicer-design.md` — realizuje `EmailSource → GmailAdapter` (sek. 3) + least-privilege (§9). Sekrety (token/client_secrets) trzymane lokalnie, w `.gitignore`.

**Stan wyjściowy:** Plany 01–05 scalone. Port `EmailSource.fetch(sender: str) -> list[InvoiceDocument]` (z Planu 02) + `FixtureSource` (offline). `InvoiceDocument(sender, received_at, filename, content: bytes, subject="")`. 103 testy + 2 skipped, ruff czysty. Praca na `feat/plan-06-gmail`. Komendy `uv run`. Importy na górze plików.

**API (zweryfikowane):** `build('gmail','v1', credentials=creds)`; `service.users().messages().list(userId='me', q='from:X has:attachment filename:pdf').execute()` → `{'messages':[{'id'}]}`; `.get(userId='me', id=ID, format='full').execute()` → `{'id','internalDate','payload':{'headers':[{'name','value'}],'parts':[...]}}`; część-załącznik ma `filename`, `mimeType`, `body.attachmentId`; `.attachments().get(userId='me', messageId=ID, id=ATT).execute()` → `{'data': <base64url>}`.

---

## File Structure

| Plik | Odpowiedzialność |
|------|------------------|
| `pyproject.toml` (MOD) | + `google-api-python-client`, `google-auth`, `google-auth-oauthlib`. |
| `.gitignore` (MOD) | + `token.json`, `client_secret*.json` (sekrety OAuth). |
| `src/invoicer/adapters/gmail.py` (NEW) | Czyste helpery (`GMAIL_SCOPES`, `_build_query`, `_header`, `_iter_pdf_parts`, `_attachment_bytes`) + `GmailAdapter` (`EmailSource`, wstrzykiwany service). |
| `src/invoicer/adapters/gmail_auth.py` (NEW) | `gmail_service_from_token` (token → service), `authorize_gmail` (interaktywny OAuth, raz). |
| `tests/unit/test_gmail.py` (NEW) | Helpery + `GmailAdapter.fetch` z fake-service + konformność portu. |
| `tests/live/test_gmail_live.py` (NEW) | live smoke (skip bez tokenu). |

---

## Task 0: Gałąź + zależności Google

- [ ] **Step 1** — `cd /Users/mski/Developer/Invoicer && git checkout main && git checkout -b feat/plan-06-gmail`.
- [ ] **Step 2** — `uv add google-api-python-client google-auth google-auth-oauthlib`. Expected: dodaje 3 zależności, aktualizuje `uv.lock`, instaluje.
- [ ] **Step 3: Sanity** — `uv run python -c "from googleapiclient.discovery import build; from google_auth_oauthlib.flow import InstalledAppFlow; from google.oauth2.credentials import Credentials; print('ok')"` → `ok`.
- [ ] **Step 4: Ignore sekretów** — w `.gitignore` dopisz dwie linie na końcu:
```gitignore
token.json
client_secret*.json
```
- [ ] **Step 5: Suite + commit** — `uv run pytest -q` (103 passed, 2 skipped), `uv run ruff check .` (clean).
```bash
git add pyproject.toml uv.lock .gitignore
git commit -m "build: add Google API client deps; ignore OAuth secrets"
```

---

## Task 1: Czyste helpery parsowania Gmaila

**Files:**
- Create: `src/invoicer/adapters/gmail.py`
- Test: `tests/unit/test_gmail.py`

- [ ] **Step 1: Write the failing test** — `tests/unit/test_gmail.py`:
```python
from invoicer.adapters.gmail import (
    GMAIL_SCOPES,
    _build_query,
    _header,
    _iter_pdf_parts,
)


def test_scope_is_readonly():
    assert GMAIL_SCOPES == ["https://www.googleapis.com/auth/gmail.readonly"]


def test_build_query_filters_sender_and_pdf_attachments():
    assert _build_query("a@b.pl") == "from:a@b.pl has:attachment filename:pdf"


def test_header_is_case_insensitive_and_missing_returns_none():
    payload = {"headers": [{"name": "From", "value": "x@y.pl"}]}
    assert _header(payload, "from") == "x@y.pl"
    assert _header(payload, "Subject") is None


def test_iter_pdf_parts_finds_nested_pdf_and_ignores_others():
    payload = {
        "parts": [
            {"mimeType": "text/plain", "filename": "", "body": {"data": "x"}},
            {
                "parts": [
                    {
                        "mimeType": "application/pdf",
                        "filename": "faktura.pdf",
                        "body": {"attachmentId": "att1"},
                    }
                ]
            },
        ]
    }
    pdfs = list(_iter_pdf_parts(payload))
    assert len(pdfs) == 1
    assert pdfs[0]["filename"] == "faktura.pdf"


def test_iter_pdf_parts_matches_pdf_by_filename_even_if_mime_octet():
    payload = {
        "parts": [
            {"mimeType": "application/octet-stream", "filename": "skan.PDF", "body": {"attachmentId": "a"}}
        ]
    }
    assert len(list(_iter_pdf_parts(payload))) == 1
```

- [ ] **Step 2: Run to verify it fails** — `uv run pytest tests/unit/test_gmail.py -v` → FAIL (`ModuleNotFoundError: No module named 'invoicer.adapters.gmail'`).

- [ ] **Step 3: Implement `src/invoicer/adapters/gmail.py`** (helpers only this task):
```python
from __future__ import annotations

import base64
from collections.abc import Iterator

GMAIL_SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]


def _build_query(sender: str) -> str:
    """Zapytanie Gmail: faktury (PDF) od konkretnego nadawcy."""
    return f"from:{sender} has:attachment filename:pdf"


def _header(payload: dict, name: str) -> str | None:
    """Wartosc naglowka (case-insensitive) z payloadu wiadomosci."""
    lowered = name.lower()
    for header in payload.get("headers", []):
        if header.get("name", "").lower() == lowered:
            return header.get("value")
    return None


def _iter_pdf_parts(payload: dict) -> Iterator[dict]:
    """Rekurencyjnie wyszukuje czesci bedace zalacznikami PDF (po MIME lub rozszerzeniu)."""
    parts = payload.get("parts")
    if parts:
        for part in parts:
            yield from _iter_pdf_parts(part)
        return
    filename = payload.get("filename", "")
    mime = payload.get("mimeType", "")
    if mime == "application/pdf" or filename.lower().endswith(".pdf"):
        yield payload


def _b64url_decode(data: str) -> bytes:
    """Dekoduje base64url z Gmaila (uzupelnia brakujacy padding)."""
    return base64.urlsafe_b64decode(data + "=" * (-len(data) % 4))
```

- [ ] **Step 4: Verify pass** — `uv run pytest tests/unit/test_gmail.py -v` → PASS (5). `uv run pytest -q` → green (108 passed, 2 skipped). `uv run ruff check . && uv run ruff format --check .` → clean.

- [ ] **Step 5: Commit**
```bash
git add src/invoicer/adapters/gmail.py tests/unit/test_gmail.py
git commit -m "feat: Gmail parsing helpers (query, headers, recursive PDF parts, base64url)"
```

---

## Task 2: GmailAdapter (wstrzykiwany service)

**Files:**
- Modify: `src/invoicer/adapters/gmail.py`
- Test: `tests/unit/test_gmail.py`

- [ ] **Step 1: Add failing tests** — in `tests/unit/test_gmail.py`, MERGE imports at top (ruff isort): add `import base64` (stdlib, first), extend the gmail import to add `GmailAdapter`, and add `from invoicer.models import InvoiceDocument` and `from invoicer.ports import EmailSource`. Then APPEND a fake Gmail service + tests:
```python
def _message_fixture() -> dict:
    return {
        "id": "m1",
        "internalDate": "1780272000000",
        "payload": {
            "headers": [
                {"name": "From", "value": "Ksiegowa <ksiegowa@klient.pl>"},
                {"name": "Subject", "value": "Faktura FV/1"},
            ],
            "parts": [
                {"mimeType": "text/plain", "filename": "", "body": {"data": "aGVq"}},
                {
                    "mimeType": "application/pdf",
                    "filename": "faktura.pdf",
                    "body": {"attachmentId": "att1"},
                },
            ],
        },
    }


class _Exec:
    def __init__(self, result):
        self._result = result

    def execute(self):
        return self._result


class _Attachments:
    def __init__(self, data):
        self._data = data

    def get(self, **_kwargs):
        return _Exec({"data": self._data})


class _Messages:
    def __init__(self, list_result, get_result, attach_data):
        self._list_result = list_result
        self._get_result = get_result
        self._attach_data = attach_data

    def list(self, **_kwargs):
        return _Exec(self._list_result)

    def get(self, **_kwargs):
        return _Exec(self._get_result)

    def attachments(self):
        return _Attachments(self._attach_data)


class _Users:
    def __init__(self, messages):
        self._messages = messages

    def messages(self):
        return self._messages


class _FakeGmail:
    def __init__(self, *, list_result, get_result, attach_data):
        self._users = _Users(_Messages(list_result, get_result, attach_data))

    def users(self):
        return self._users


def test_gmail_adapter_satisfies_email_source_protocol():
    service = _FakeGmail(list_result={}, get_result=None, attach_data="")
    assert isinstance(GmailAdapter(service), EmailSource)


def test_fetch_builds_invoice_document_from_pdf_attachment():
    pdf = b"%PDF-1.4 dane"
    # Gmail zwraca base64url; symulujemy brak paddingu, by sprawdzic uzupelnianie.
    b64 = base64.urlsafe_b64encode(pdf).decode().rstrip("=")
    service = _FakeGmail(
        list_result={"messages": [{"id": "m1"}]},
        get_result=_message_fixture(),
        attach_data=b64,
    )
    docs = GmailAdapter(service).fetch("ksiegowa@klient.pl")
    assert len(docs) == 1
    doc = docs[0]
    assert isinstance(doc, InvoiceDocument)
    assert doc.content == pdf
    assert doc.filename == "faktura.pdf"
    assert "ksiegowa@klient.pl" in doc.sender
    assert doc.subject == "Faktura FV/1"


def test_fetch_returns_empty_when_no_messages():
    service = _FakeGmail(list_result={}, get_result=None, attach_data="")
    assert GmailAdapter(service).fetch("nikt@x.pl") == []
```

- [ ] **Step 2: Run to verify it fails** — `uv run pytest tests/unit/test_gmail.py -k "adapter or fetch" -v` → FAIL (`ImportError: cannot import name 'GmailAdapter'`).

- [ ] **Step 3: Implement** — append to `src/invoicer/adapters/gmail.py`. Add to the top imports: `from datetime import UTC, datetime` (stdlib) and `from invoicer.models import InvoiceDocument` (first-party). Append:
```python
def _attachment_bytes(service, user_id: str, message_id: str, part: dict) -> bytes:
    """Pobiera bajty zalacznika: z body.data jesli inline, inaczej przez attachments().get."""
    body = part.get("body", {})
    data = body.get("data")
    if data is None:
        attachment_id = body["attachmentId"]
        data = (
            service.users()
            .messages()
            .attachments()
            .get(userId=user_id, messageId=message_id, id=attachment_id)
            .execute()["data"]
        )
    return _b64url_decode(data)


class GmailAdapter:
    """EmailSource oparty o Gmail API. `service` jest wstrzykiwany (CI: fake; realny: build()).

    Pobiera wiadomosci od `sender` z zalacznikami PDF i mapuje je na InvoiceDocument.
    """

    def __init__(self, service, *, user_id: str = "me") -> None:
        self._service = service
        self._user_id = user_id

    def fetch(self, sender: str) -> list[InvoiceDocument]:
        messages = self._service.users().messages()
        listing = messages.list(userId=self._user_id, q=_build_query(sender)).execute()
        docs: list[InvoiceDocument] = []
        for ref in listing.get("messages", []):
            msg = messages.get(userId=self._user_id, id=ref["id"], format="full").execute()
            payload = msg["payload"]
            from_header = _header(payload, "From") or sender
            subject = _header(payload, "Subject") or ""
            received_at = datetime.fromtimestamp(int(msg["internalDate"]) / 1000, tz=UTC)
            for part in _iter_pdf_parts(payload):
                content = _attachment_bytes(self._service, self._user_id, ref["id"], part)
                docs.append(
                    InvoiceDocument(
                        sender=from_header,
                        subject=subject,
                        received_at=received_at,
                        filename=part.get("filename", "attachment.pdf"),
                        content=content,
                    )
                )
        return docs
```

- [ ] **Step 4: Verify pass** — `uv run pytest tests/unit/test_gmail.py -v` → PASS (8). `uv run pytest -q` → green (111 passed, 2 skipped). `uv run ruff check . && uv run ruff format --check .` → clean.

- [ ] **Step 5: Commit**
```bash
git add src/invoicer/adapters/gmail.py tests/unit/test_gmail.py
git commit -m "feat: GmailAdapter.fetch (injectable service, PDF attachments -> InvoiceDocument)"
```

---

## Task 3: OAuth / budowa serwisu + live smoke

**Files:**
- Create: `src/invoicer/adapters/gmail_auth.py`
- Create: `tests/live/test_gmail_live.py`

- [ ] **Step 1: Implement `src/invoicer/adapters/gmail_auth.py`**

Auth jest z natury manualny/sieciowy (OAuth) — importy bibliotek Google trzymamy leniwie wewnątrz funkcji, by moduł importował się też bez nich w innych kontekstach. Treść:
```python
from __future__ import annotations

from pathlib import Path

from invoicer.adapters.gmail import GMAIL_SCOPES


def gmail_service_from_token(token_path: Path, *, scopes: list[str] | None = None):
    """Buduje zasob Gmail API z zapisanego tokenu (odswieza, jesli wygasl).

    Wymaga wczesniejszego `authorize_gmail` (jednorazowy OAuth). Sieciowe — nie w CI.
    """
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from googleapiclient.discovery import build

    creds = Credentials.from_authorized_user_file(str(token_path), scopes or GMAIL_SCOPES)
    if creds.expired and creds.refresh_token:
        creds.refresh(Request())
    return build("gmail", "v1", credentials=creds, cache_discovery=False)


def authorize_gmail(
    client_secrets_path: Path, token_path: Path, *, scopes: list[str] | None = None
) -> None:
    """Jednorazowy interaktywny OAuth (otwiera przegladarke). Zapisuje token do `token_path`.

    Uzycie (raz, lokalnie):
        python -c "from pathlib import Path; from invoicer.adapters.gmail_auth import authorize_gmail; \\
                   authorize_gmail(Path('client_secret.json'), Path('token.json'))"
    Pobierz `client_secret.json` z Google Cloud Console (OAuth client, typ Desktop).
    """
    from google_auth_oauthlib.flow import InstalledAppFlow

    flow = InstalledAppFlow.from_client_secrets_file(
        str(client_secrets_path), scopes or GMAIL_SCOPES
    )
    creds = flow.run_local_server(port=0)
    token_path.write_text(creds.to_json(), encoding="utf-8")
```

- [ ] **Step 2: Live smoke** — create `tests/live/test_gmail_live.py`:
```python
import os
from pathlib import Path

import pytest

from invoicer.adapters.gmail import GmailAdapter
from invoicer.adapters.gmail_auth import gmail_service_from_token

_TOKEN = Path(os.getenv("GMAIL_TOKEN", "token.json"))
_SENDER = os.getenv("GMAIL_SENDER_FILTER", "")

pytestmark = pytest.mark.skipif(
    not _TOKEN.exists() or not _SENDER,
    reason="wymaga token.json (po authorize_gmail) + GMAIL_SENDER_FILTER (test live)",
)


def test_real_gmail_fetch_returns_documents():
    service = gmail_service_from_token(_TOKEN)
    docs = GmailAdapter(service).fetch(_SENDER)
    assert isinstance(docs, list)
    for doc in docs:
        assert doc.filename.lower().endswith(".pdf")
        assert doc.content[:4] == b"%PDF"
```

- [ ] **Step 3: Confirm collected-but-skipped** — `uv run pytest tests/live -v` → `3 skipped` (extractor + reasoner + gmail). MUST NOT error on collection (gmail_auth imports lazily, so no Google import at collection).

- [ ] **Step 4: Lint + full suite** — `uv run ruff check . && uv run ruff format --check .` → clean. `uv run pytest -q` → expect **111 passed, 3 skipped**.

- [ ] **Step 5: Commit**
```bash
git add src/invoicer/adapters/gmail_auth.py tests/live/test_gmail_live.py
git commit -m "feat: Gmail OAuth/service factory + live-gated fetch smoke"
```

---

## Task 4: Lint + finał + merge-ready

- [ ] **Step 1: Ruff** — `cd /Users/mski/Developer/Invoicer && uv run ruff check . && uv run ruff format --check .` → clean.
- [ ] **Step 2: Pełny suite** — `uv run pytest -q` → **111 passed, 3 skipped** (zweryfikuj realne liczby; Plan 05 = 103+2 → +8 unit gmail = 111, +1 skipped live).
- [ ] **Step 3: Commit porządkowy (jeśli ruff coś zmienił)** — `git add -A && git commit -m "chore: ruff clean, green suite (Plan 06 Gmail done)" || echo "nic do commita"`.

> **Demo (poza CI):** 1) w Google Cloud Console utwórz projekt + OAuth client (typ *Desktop*) → pobierz `client_secret.json`; 2) `authorize_gmail(Path('client_secret.json'), Path('token.json'))` (otworzy przeglądarkę, zaloguj się, scope read-only); 3) podmień w grafie: `build_invoice_graph(...)` zasilany dokumentami z `GmailAdapter(gmail_service_from_token(Path('token.json'))).fetch(sender)`. Token/secret są w `.gitignore`.

---

## Self-Review (wykonane przy pisaniu planu)

**Spec coverage (Plan 06 = `GmailAdapter`; sek. 3 + §9 least-privilege):**
- Czyste helpery (query, naglowki, rekurencyjne czesci PDF, base64url) → Task 1 ✓
- `GmailAdapter.fetch` (wstrzykiwany service, PDF → InvoiceDocument) → Task 2 ✓
- OAuth read-only (`gmail.readonly`) + factory serwisu + jednorazowy authorize → Task 3 ✓ (least privilege §9)
- Sekrety (`token.json`, `client_secret*.json`) w `.gitignore` → Task 0 ✓
- Live smoke (skip bez tokenu) → Task 3 ✓
- Podmiana w grafie bez zmian rdzenia (port `EmailSource`) → istniejący szew (P02), demo w nocie.

**Placeholder scan:** brak TBD/TODO; pełny kod + komendy. Auth jest manualny (OAuth interaktywny) — to nie placeholder, lecz świadoma granica; testowalna część (fetch) w pełni w CI.

**Type consistency:** `_build_query(sender)->str`, `_header(payload,name)->str|None`, `_iter_pdf_parts(payload)->Iterator[dict]`, `_b64url_decode(str)->bytes`, `_attachment_bytes(service,user_id,message_id,part)->bytes`, `GmailAdapter(service, *, user_id="me").fetch(sender)->list[InvoiceDocument]` (zgodny z portem `EmailSource`); `gmail_service_from_token(token_path, *, scopes=None)`; `authorize_gmail(client_secrets_path, token_path, *, scopes=None)`. Fake-service odwzorowuje łańcuch `users().messages().list/get/attachments().execute()`.

**Uwaga wykonawcza:** `fetch()` w pełni testowalne w CI dzięki fake-service (`_FakeGmail`); realny kontakt z Google API tylko w skip-owanym teście live. Importy bibliotek Google leniwie w `gmail_auth.py`, więc kolekcja testów nie wymaga skonfigurowanego OAuth.
