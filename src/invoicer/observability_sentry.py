from __future__ import annotations

from typing import Any

from invoicer.security import redact_pii


def _redact_obj(obj: Any) -> Any:
    """Rekursywnie redaguje PII we wszystkich stringach (dict/list/tuple/str).

    Pokrywa typy, ktore produkuje Sentry LoggingIntegration (str/dict/list + skalary).
    Inne skalary (int/float/bool/None) przechodza bez zmian; bytes/set NIE sa rekurowane —
    nieosiagalne w obecnym kodzie (jedyny punkt to sentry_sdk.init, brak custom scope).
    """
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
