from __future__ import annotations

import re

_ACCOUNT = re.compile(r"\b\d{26}\b")  # rachunek PL (26 cyfr)
_NIP = re.compile(r"\b\d{10}\b")  # NIP (10 cyfr)
_EMAIL = re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b")


def redact_pii(text: str) -> str:
    """Maskuje dane wrazliwe (rachunek, NIP, e-mail) w tekscie przeznaczonym do logow.

    Spec §9: kroki rozumujace / logi nie powinny wyciekac PII.
    """
    text = _ACCOUNT.sub("[KONTO]", text)  # najpierw 26 cyfr, by nie zlapac jako NIP
    text = _NIP.sub("[NIP]", text)
    return _EMAIL.sub("[EMAIL]", text)
