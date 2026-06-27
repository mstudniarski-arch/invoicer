from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import datetime

from langgraph.types import interrupt

from invoicer.booking import invoice_to_booking_payload
from invoicer.ledger import Ledger, LedgerEntry
from invoicer.models import Classification, CountryBucket, GroundingStatus, TaxTreatment
from invoicer.ports import AccountingSink, ExceptionReasoner, InvoiceExtractor, LegalKnowledgeStore
from invoicer.rag.query import build_retrieval_query
from invoicer.state import InvoiceState
from invoicer.validation import validate_invoice

_logger = logging.getLogger("invoicer.graph")

LOW_CONFIDENCE = 0.6
RELEVANCE_THRESHOLD = 0.5
CONFIDENCE_CAP_WEAK = 0.4
CONFIDENCE_CAP_UNSUPPORTED = 0.3


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


def make_retrieve_legal_context_node(
    store: LegalKnowledgeStore, *, k: int = 5, threshold: float = RELEVANCE_THRESHOLD
):
    """Wezel `retrieve_legal_context`: pobiera trafne przepisy z bazy wektorowej.

    Query budowane z allowlisty (bez PII). Fragmenty ponizej progu trafnosci odrzucane;
    pusta lista = sygnal do abstention w reason_exception.
    """

    def retrieve_legal_context(state: InvoiceState) -> dict:
        query = build_retrieval_query(state["invoice"])
        hits = store.search(query, k=k)
        relevant = [h for h in hits if h.score >= threshold]
        return {"legal_context": relevant}

    return retrieve_legal_context


def make_validate_node(ledger: Ledger):
    """Wezel `validate`: deterministyczna walidacja + wykrywanie duplikatow (ledger)."""

    def validate(state: InvoiceState) -> dict:
        return {"validation": validate_invoice(state["invoice"], ledger=ledger)}

    return validate


def route_after_validate(state: InvoiceState) -> str:
    """Krawedz warunkowa po validate: duplikat (juz zaksiegowany) -> END, inaczej -> classify.

    Idempotencja: faktura wykryta jako duplikat w ledger nie ma po co isc do czlowieka
    ani do ksiegowania — pomijamy cicho (log). Straznik w `book` zostaje jako defense-in-depth.
    Zwykle bledy walidacji (NIP/sumy) NIE sa duplikatem -> nadal ida do human_review.
    """
    if state["validation"].is_duplicate:
        invoice = state.get("invoice")
        number = invoice.number if invoice is not None else "?"
        _logger.info("validate: faktura %s juz zaksiegowana — pomijam (duplikat)", number)
        return "end"
    return "classify"


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
    z lista rzeczy do potwierdzenia przez czlowieka.
    Faktury zagraniczne ida przez retrieve_legal_context -> reason_exception (grounded)
    -> verify_grounding.
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


def human_review(state: InvoiceState) -> dict:
    """Wezel `human_review`: zatrzymuje graf (interrupt) i czeka na decyzje czlowieka.

    Zwracana wartosc resume (Command(resume=...)) trafia do human_decision.
    """
    invoice = state["invoice"]
    validation = state["validation"]
    classification = state["classification"]
    payload = {
        "number": invoice.number,
        "seller": invoice.seller.name,
        "seller_nip": invoice.seller.nip,
        "country": invoice.seller.country,
        "total_gross": str(invoice.total_gross),
        "currency": invoice.currency,
        "validation_ok": validation.ok,
        "flags": [c.name for c in validation.hard_errors] + list(state.get("errors", [])),
        "treatment": str(classification.treatment),
        "rationale": classification.rationale_pl,
        "must_confirm": classification.human_must_confirm,
        "grounding_status": str(classification.grounding_status),
        "citations": [c.article_ref for c in classification.citations],
    }
    decision = interrupt(payload)
    return {"human_decision": decision}


def route_after_review(state: InvoiceState) -> str:
    """Krawedz warunkowa po human_review: tylko 'approve' prowadzi do ksiegowania."""
    return "book" if state.get("human_decision") == "approve" else "end"


def make_book_node(sink: AccountingSink, ledger: Ledger, clock: Callable[[], str] | None = None):
    """Wezel `book`: mapuje na dekret, ksieguje (sink) i dopisuje do ledger (audyt + duplikaty)."""
    clock = clock or (lambda: datetime.now().isoformat(timespec="seconds"))

    def book(state: InvoiceState) -> dict:
        invoice = state["invoice"]
        if ledger.is_duplicate(invoice.number, invoice.seller.nip, invoice.seller.name):
            raise RuntimeError(
                f"Faktura {invoice.number} jest juz zaksiegowana — przerwano podwojne ksiegowanie"
            )
        classification = state["classification"]
        payload = invoice_to_booking_payload(invoice, treatment=str(classification.treatment))
        result = sink.post(payload)
        ledger.append(
            LedgerEntry(
                number=invoice.number,
                seller_nip=invoice.seller.nip,
                seller_name=invoice.seller.name,
                total_gross=str(invoice.total_gross),
                booking_id=result.booking_id,
                booked_at=clock(),
            )
        )
        return {"booking": result}

    return book


def make_reason_exception_node(reasoner: ExceptionReasoner):
    """Wezel `reason_exception`: grounded generation, albo abstention gdy brak kontekstu prawnego.

    Gdy legal_context jest puste (retrieval nic nie zwrocil), wezel NIE wywoluje LLM —
    zachowuje deterministyczny prior, ustawia grounding_status=WEAK i ogranicza confidence.
    """

    def reason_exception(state: InvoiceState) -> dict:
        base = state["classification"]
        context = state.get("legal_context", [])
        if not context:
            weak = base.model_copy(
                update={
                    "grounding_status": GroundingStatus.WEAK,
                    "confidence": min(base.confidence, CONFIDENCE_CAP_WEAK),
                    "human_must_confirm": [
                        *base.human_must_confirm,
                        "brak wystarczajacej podstawy prawnej w bazie — wymaga recznej weryfikacji",
                    ],
                }
            )
            return {"classification": weak}
        enriched = reasoner.reason(state["invoice"], base, context)
        return {"classification": enriched}

    return reason_exception


def route_after_classify(state: InvoiceState) -> str:
    """Krawedz po classify: PL -> human_review; zagranica -> retrieve_legal_context (RAG)."""
    if state["classification"].country_bucket == CountryBucket.PL:
        return "human_review"
    return "retrieve_legal_context"


def _normalize(text: str) -> str:
    return " ".join(text.split()).casefold()


def _span_supported(span: str, source_text: str) -> bool:
    return _normalize(span) in _normalize(source_text)


def make_verify_grounding_node():
    """Wezel `verify_grounding`: deterministyczny faithfulness-check cytatow (span-containment).

    Cytat niepoparty zrodlem (lub brak cytatow) -> grounding_status=unsupported + cap pewnosci
    + flaga do czlowieka. Abstention (weak) przepuszczamy bez zmian. LLM-entailment: Plan 03.
    """

    def verify_grounding(state: InvoiceState) -> dict:
        classification = state["classification"]
        if classification.grounding_status == GroundingStatus.WEAK:
            return {"classification": classification}  # abstention juz ustawione w reason_exception
        by_ref = {(c.source_id, c.article_ref): c.text for c in state.get("legal_context", [])}
        unsupported = [
            cit.article_ref
            for cit in classification.citations
            if not _span_supported(
                cit.quoted_span, by_ref.get((cit.source_id, cit.article_ref), "")
            )
        ]
        if not classification.citations or unsupported:
            detail = ", ".join(unsupported) if unsupported else "brak cytatow"
            updated = classification.model_copy(
                update={
                    "grounding_status": GroundingStatus.UNSUPPORTED,
                    "confidence": min(classification.confidence, CONFIDENCE_CAP_UNSUPPORTED),
                    "human_must_confirm": [
                        *classification.human_must_confirm,
                        f"cytaty niepotwierdzone w zrodle: {detail}",
                    ],
                }
            )
            return {"classification": updated}
        return {
            "classification": classification.model_copy(
                update={"grounding_status": GroundingStatus.GROUNDED}
            )
        }

    return verify_grounding
