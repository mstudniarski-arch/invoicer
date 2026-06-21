from __future__ import annotations

import logging
import re

# Konto / IBAN — od najdluzszych/najbardziej specyficznych (kolejnosc ma znaczenie):
_IBAN_GROUPED = re.compile(r"\b(?:[Pp][Ll])?\d{2}(?:[ ]\d{4}){6}\b")  # NRB/IBAN w grupach po 4
_IBAN_PL = re.compile(r"\b[Pp][Ll]\d{26}\b")  # IBAN PL zwarty (z prefiksem PL)
_ACCOUNT = re.compile(r"\b\d{26}\b")  # NRB zwarty (26 cyfr)

# NIP:
# NIP z myslnikami (format NNN-NNN-NN-NN lub NNN-NN-NN-NNN):
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


class RedactingFilter(logging.Filter):
    """Filtr logowania, ktory maskuje PII w finalnej tresci rekordu.

    Operuje na `record.getMessage()` (renderuje %s-argumenty PRZED redakcja), wiec lapie
    PII przekazane przez `record.args` (tak loguje MockSubiektSink). Po redakcji czysci
    `args`, by handlery nie re-renderowaly surowych wartosci. Zawsze przepuszcza rekord
    (filtr transformujacy, nie odrzucajacy). Bezpieczny przy wielu handlerach dzieki
    idempotencji `redact_pii`.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            record.msg = redact_pii(record.getMessage())
            record.args = ()
        except Exception:  # logowanie nie moze wywalic aplikacji
            pass  # zly format/args -> zostaw rekord; handler obsluzy przez handleError
        return True


def install_redaction(logger: logging.Logger | None = None) -> None:
    """Podpina RedactingFilter do handlerow loggera (domyslnie root) — idempotentnie.

    Redaguje WSZYSTKIE rekordy docierajace do handlera (rowniez child-loggery `invoicer.*`
    oraz przyszly SubiektSferaSink i logi third-party — over-masking jest bezpieczny).
    Jesli cel nie ma handlera, dodaje StreamHandler. Wolaj PO konfiguracji logowania aplikacji.
    """
    target = logger if logger is not None else logging.getLogger()
    if not target.handlers:
        target.addHandler(logging.StreamHandler())
    for handler in target.handlers:
        if not any(isinstance(flt, RedactingFilter) for flt in handler.filters):
            handler.addFilter(RedactingFilter())
