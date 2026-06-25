from __future__ import annotations

import logging
from typing import Any

from invoicer.security import redact_pii

_logger = logging.getLogger("invoicer.alerts")


def format_failure_alert(context: str, reason: str) -> str:
    """Krotka tresc alertu o porazce (idzie na WhatsApp wlasciciela)."""
    return f"⚠️ {context}: {reason}"


def send_failure_alert(channel: Any, text: str) -> None:
    """Wysyla alert przez kanal (channel.notify). NIGDY nie rzuca — blad kanalu tylko logujemy.

    Tresc przechodzi przez redact_pii: tekst alertu (np. str(exc)) nie wynosi PII/SID na WhatsApp.
    """
    try:
        channel.notify(redact_pii(text))
    except Exception as exc:  # noqa: BLE001 - alert nie moze wywalic pipeline'u
        _logger.error("alert nieudany: %s", redact_pii(str(exc)))
