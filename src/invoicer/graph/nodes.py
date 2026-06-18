from __future__ import annotations

from invoicer.ledger import Ledger
from invoicer.models import Classification, CountryBucket, TaxTreatment
from invoicer.ports import InvoiceExtractor
from invoicer.state import InvoiceState
from invoicer.validation import validate_invoice

LOW_CONFIDENCE = 0.6


def make_extract_node(extractor: InvoiceExtractor):
    """Wezel `extract`: surowy dokument -> Invoice (przez wstrzykniety ekstraktor)."""

    def extract(state: InvoiceState) -> dict:
        # Licznik 'absolutny': czytaj biezacy + 1 i zwroc wartosc. InvoiceState.extract_attempts
        # celowo NIE ma reducera (domyslny LastValue/nadpisanie) — przy ewentualnej petli retry
        # odczyt-inkrementacja-nadpisanie daje poprawna kumulacje. operator.add zepsuloby to.
        attempts = state.get("extract_attempts", 0) + 1
        invoice = extractor.extract(state["document"])
        update: dict = {"invoice": invoice, "extract_attempts": attempts}
        conf = invoice.extraction_confidence
        if conf is not None and conf < LOW_CONFIDENCE:
            update["errors"] = [f"Niska pewnosc ekstrakcji: {conf:.2f}"]
        return update

    return extract


def make_validate_node(ledger: Ledger):
    """Wezel `validate`: deterministyczna walidacja + wykrywanie duplikatow (ledger)."""

    def validate(state: InvoiceState) -> dict:
        return {"validation": validate_invoice(state["invoice"], ledger=ledger)}

    return validate


# 27 panstw UE (zawiera PL). Sprzedawca z PL jest obslugiwany wczesniej (galaz country == "PL"),
# wiec tu zbior sluzy tylko do rozroznienia UE vs poza-UE dla sprzedawcow zagranicznych.
EU_COUNTRIES = frozenset(
    {
        "AT",
        "BE",
        "BG",
        "HR",
        "CY",
        "CZ",
        "DK",
        "EE",
        "FI",
        "FR",
        "DE",
        "GR",
        "HU",
        "IE",
        "IT",
        "LV",
        "LT",
        "LU",
        "MT",
        "NL",
        "PL",
        "PT",
        "RO",
        "SK",
        "SI",
        "ES",
        "SE",
    }
)


def classify_node(state: InvoiceState) -> dict:
    """Wezel `classify`: deterministyczne traktowanie podatkowe wg kraju sprzedawcy.

    PL -> krajowa. Zagranica -> domyslnie import uslug (odwrotne obciazenie),
    z lista rzeczy do potwierdzenia przez czlowieka. Bogate rozumowanie LLM
    (reason_exception) dochodzi w Planie 04.
    """
    invoice = state["invoice"]
    country = invoice.seller.country.upper()
    if country == "PL":
        classification = Classification(
            treatment=TaxTreatment.KRAJOWA,
            country_bucket=CountryBucket.PL,
            rationale_pl="Sprzedawca z PL — faktura krajowa.",
        )
    else:
        bucket = CountryBucket.UE if country in EU_COUNTRIES else CountryBucket.POZA_UE
        currency_note = (
            ""
            if invoice.currency == "PLN"
            else f"Waluta {invoice.currency} — przelicz po kursie NBP."
        )
        classification = Classification(
            treatment=TaxTreatment.IMPORT_USLUG,
            country_bucket=bucket,
            confidence=0.6,
            rationale_pl=("Sprzedawca zagraniczny — domyslnie import uslug (odwrotne obciazenie)."),
            human_must_confirm=[
                "usluga czy towar?",
                "stawka do samonaliczenia (zwykle 23%)",
                "kurs waluty (NBP z dnia poprzedzajacego)",
            ],
            currency_note=currency_note,
        )
    return {"classification": classification}
