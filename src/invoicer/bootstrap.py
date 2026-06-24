from __future__ import annotations

import base64
import binascii
import os
from pathlib import Path


def bootstrap_gmail_token(env_name: str, dest: Path) -> None:
    """Dekoduje base64 token Gmaila z env do pliku (idempotent; brak env -> no-op).

    Headless OAuth gotcha: token.json (z refresh-tokenem) generujemy lokalnie,
    a w kontenerze wstrzykujemy jako sekret GMAIL_TOKEN_B64; tu odtwarzamy plik.
    Nie nadpisuje istniejacego pliku — chroni przed regresja po restarcie.
    """
    payload = os.environ.get(env_name)
    if not payload:
        return
    if dest.exists():
        return
    try:
        raw = base64.b64decode(payload, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ValueError(f"{env_name}: niepoprawny base64") from exc
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(raw)
