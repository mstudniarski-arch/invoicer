from __future__ import annotations

import re

# Konto / IBAN — od najdluzszych/najbardziej specyficznych (kolejnosc ma znaczenie):
_IBAN_GROUPED = re.compile(r"\b(?:[Pp][Ll])?\d{2}(?:[ ]\d{4}){6}\b")  # NRB/IBAN w grupach po 4
_IBAN_PL = re.compile(r"\b[Pp][Ll]\d{26}\b")  # IBAN PL zwarty (z prefiksem PL)
_ACCOUNT = re.compile(r"\b\d{26}\b")  # NRB zwarty (26 cyfr)

# NIP:
# NIP z myslnikami (format X-XXX-XX-XX lub XXX-XX-XX-XXX):
_NIP_SEP = re.compile(r"\b\d{3}-\d{3}-\d{2}-\d{2}\b|\b\d{3}-\d{2}-\d{2}-\d{3}\b")
_NIP = re.compile(r"\b\d{10}\b")  # NIP zwarty (10 cyfr)

# E-mail:
_EMAIL = re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b")


def redact_pii(text: str) -> str:
    """Maskuje dane wrazliwe (rachunek/IBAN, NIP, e-mail) w tekscie przeznaczonym do logow.

    Spec sek. 9: kroki rozumujace / logi nie powinny wyciekac PII. Kolejnosc: konto/IBAN
    (grupowane -> z prefiksem PL -> zwarte) przed NIP (z separatorami -> zwarty), na koncu
    e-mail. Specyficzne grupowania (nie generyczne "cyfry+separatory") nie lapia dat ISO.
    Funkcja jest idempotentna: redact_pii(redact_pii(x)) == redact_pii(x).
    """
    text = _IBAN_GROUPED.sub("[KONTO]", text)
    text = _IBAN_PL.sub("[KONTO]", text)
    text = _ACCOUNT.sub("[KONTO]", text)
    text = _NIP_SEP.sub("[NIP]", text)
    text = _NIP.sub("[NIP]", text)
    return _EMAIL.sub("[EMAIL]", text)
