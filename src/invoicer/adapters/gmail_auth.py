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
        from pathlib import Path
        from invoicer.adapters.gmail_auth import authorize_gmail
        authorize_gmail(Path("client_secret.json"), Path("token.json"))
    Pobierz `client_secret.json` z Google Cloud Console (OAuth client, typ Desktop).
    """
    from google_auth_oauthlib.flow import InstalledAppFlow

    flow = InstalledAppFlow.from_client_secrets_file(
        str(client_secrets_path), scopes or GMAIL_SCOPES
    )
    creds = flow.run_local_server(port=0)
    token_path.write_text(creds.to_json(), encoding="utf-8")
