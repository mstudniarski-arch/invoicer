from __future__ import annotations

import sys
from pathlib import Path

# Umozliwia uruchomienie jako skrypt:  uv run src/invoicer/adapters/gmail_auth.py
# Projekt nie jest instalowany jako pakiet (pytest uzywa pythonpath=src) — przy starcie
# przez sciezke pliku dokladamy katalog `src` recznie, zeby `import invoicer` zadzialal.
_SRC = Path(__file__).resolve().parents[2]
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from invoicer.adapters.gmail import GMAIL_SCOPES  # noqa: E402  (po bootstrapie sys.path)


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

    Uzycie (raz, lokalnie) — najprosciej jako skrypt z katalogu repo:
        uv run src/invoicer/adapters/gmail_auth.py
    (opcjonalnie wlasne sciezki dopisane na koncu: ... gmail_auth.py SECRET.json TOKEN.json)
    Pobierz `client_secret.json` z Google Cloud Console (OAuth client, typ Desktop).
    """
    from google_auth_oauthlib.flow import InstalledAppFlow

    flow = InstalledAppFlow.from_client_secrets_file(
        str(client_secrets_path), scopes or GMAIL_SCOPES
    )
    creds = flow.run_local_server(port=0)
    token_path.write_text(creds.to_json(), encoding="utf-8")


if __name__ == "__main__":
    # Jednorazowa autoryzacja Gmaila:
    #   uv run src/invoicer/adapters/gmail_auth.py [client_secret.json] [token.json]
    # Otwiera przegladarke, prosi o zgode i zapisuje token.json ze scope = GMAIL_SCOPES.
    secret = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("client_secret.json")
    token = Path(sys.argv[2]) if len(sys.argv) > 2 else Path("token.json")
    authorize_gmail(secret, token)
    print(f"OK: token zapisany do {token.resolve()} (scope: {GMAIL_SCOPES})")
