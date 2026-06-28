from __future__ import annotations

import logging
import os
import sqlite3
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.types import Command

from invoicer.adapters.mock_subiekt import MockSubiektSink
from invoicer.adapters.stub_extractor import StubExtractor
from invoicer.adapters.stub_reasoner import IdentityReasoner
from invoicer.booking import BookingResult
from invoicer.graph.build import build_invoice_graph
from invoicer.ledger import Ledger
from invoicer.models import (
    Check,
    CheckStatus,
    Citation,
    Classification,
    CountryBucket,
    GroundingStatus,
    Invoice,
    InvoiceDocument,
    LineItem,
    Party,
    TaxTreatment,
    ValidationResult,
)
from invoicer.ports import EmailSource, InvoiceDetector
from invoicer.rag.models import RetrievedChunk
from invoicer.state import InvoiceState

_logger = logging.getLogger("invoicer.runner")

# Typy wstawiane do stanu grafu (InvoiceState) — jawnie rejestrowane w serializerze
# checkpointu LangGraph. Bez tej allowlisty default to "warn-but-allow"; w przyszlej
# wersji LangGraph (LANGGRAPH_STRICT_MSGPACK=true) nieuznane typy zostana ZABLOKOWANE
# i wrocą jako raw dict — co rozwali resume HITL (approve/reject po WhatsApp).
_CHECKPOINT_ALLOWED_TYPES = (
    InvoiceDocument,
    Invoice,
    LineItem,
    Party,
    Check,
    CheckStatus,
    ValidationResult,
    Classification,
    CountryBucket,
    TaxTreatment,
    BookingResult,
    Citation,
    GroundingStatus,
    RetrievedChunk,
)


def _run_config(thread_id: str) -> dict:
    """Config przebiegu: thread_id (klucz checkpointera) + nazwa/tagi/metadane do tracingu.

    run_name/tags/metadata sa uzywane przez LangSmith (gdy wlaczony) do nazwania i odfiltrowania
    przebiegu po fakturze; przy wylaczonym tracingu sa po prostu ignorowane.
    """
    return {
        "configurable": {"thread_id": thread_id},
        "run_name": f"invoice-{thread_id}",
        "tags": ["invoicer"],
        "metadata": {"thread_id": thread_id},
    }


def start_document(graph, document: InvoiceDocument, *, thread_id: str) -> dict | None:
    """Uruchamia dokument w grafie do bramki human_review; zwraca payload interrupt (lub None)."""
    config = _run_config(thread_id)
    result = graph.invoke({"document": document, "errors": []}, config)
    interrupts = result.get("__interrupt__")
    return interrupts[0].value if interrupts else None


def resume_document(graph, *, thread_id: str, decision: str) -> InvoiceState:
    """Wznawia graf po decyzji czlowieka (approve/reject/edit)."""
    config = _run_config(thread_id)
    return graph.invoke(Command(resume=decision), config)


def document_from_upload(
    filename: str, content: bytes, *, sender: str = "demo@upload"
) -> InvoiceDocument:
    """Owija wgrany plik w InvoiceDocument (received_at = teraz)."""
    return InvoiceDocument(
        sender=sender, received_at=datetime.now(UTC), filename=filename, content=content
    )


def _demo_invoice() -> Invoice:
    """Przykladowa faktura PL do trybu offline (gdy brak ANTHROPIC_API_KEY)."""
    line = LineItem(
        description="Usluga programistyczna (DEMO offline)",
        quantity=Decimal("1"),
        unit_net=Decimal("1000.00"),
        vat_rate=Decimal("0.23"),
        net=Decimal("1000.00"),
        vat=Decimal("230.00"),
        gross=Decimal("1230.00"),
    )
    return Invoice(
        seller=Party(name="ACME sp. z o.o.", nip="5260001246", country="PL"),
        buyer=Party(name="Klient sp. z o.o.", country="PL"),
        number="FV/DEMO/1",
        issue_date=datetime.now(UTC).date(),
        currency="PLN",
        lines=[line],
        total_net=Decimal("1000.00"),
        total_vat=Decimal("230.00"),
        total_gross=Decimal("1230.00"),
        extraction_confidence=0.95,
    )


def fetch_invoice_documents(
    source: EmailSource, detector: InvoiceDetector, sender: str
) -> list[InvoiceDocument]:
    """Pobiera dokumenty (EmailSource) i zostawia tylko wykryte jako faktura (InvoiceDetector).

    Pre-filtr ('kontynuuj proces tylko dla faktur'): kazda zwrocona fakture wolajacy
    karmi przez start_document -> human_review (bez auto-approve).
    """
    return [doc for doc in source.fetch(sender) if detector.is_invoice(doc)]


def build_legal_store():
    """Realny PgVectorLegalStore (Voyage + rerank) gdy DATABASE_URL; inaczej pusty store.

    Pusty InMemoryLegalStore => brak kontekstu => abstention (graf dziala bez bazy/kluczy).
    """
    if os.getenv("DATABASE_URL"):
        from invoicer.adapters.pgvector_store import PgVectorLegalStore
        from invoicer.adapters.voyage_embedder import VoyageEmbedder
        from invoicer.adapters.voyage_reranker import VoyageReranker

        return PgVectorLegalStore(VoyageEmbedder(), reranker=VoyageReranker())
    from invoicer.adapters.fake_embedder import DeterministicEmbedder
    from invoicer.adapters.in_memory_legal_store import InMemoryLegalStore

    return InMemoryLegalStore(DeterministicEmbedder())


def active_sink_name() -> str:
    """Nazwa aktywnego AccountingSink wg env (bez budowania) — do logu startu i /status."""
    if os.getenv("INVOICER_SINK", "").lower() == "fakturownia":
        return "fakturownia"
    return "mock-subiekt"


def build_sink():
    """AccountingSink wg env: FakturowniaSink gdy INVOICER_SINK=fakturownia, inaczej MockSubiekt.

    Fakturownia ksieguje fakture jako KOSZT (income=0) — widoczne w .../invoices?income=no.
    Wymaga FAKTUROWNIA_API_TOKEN + FAKTUROWNIA_DOMAIN. Loguje wybor (provenance konfiguracji).
    """
    _logger.info("AccountingSink aktywny: %s", active_sink_name())
    if os.getenv("INVOICER_SINK", "").lower() == "fakturownia":
        from invoicer.adapters.fakturownia import build_fakturownia_sink

        return build_fakturownia_sink()
    return MockSubiektSink()


def build_demo_graph(*, ledger_path: Path):
    """Buduje graf demo: realny Claude gdy ANTHROPIC_API_KEY, inaczej offline (stub)."""
    if os.getenv("ANTHROPIC_API_KEY"):
        from invoicer.adapters.claude_extractor import ClaudeVisionExtractor
        from invoicer.adapters.claude_reasoner import ClaudeExceptionReasoner

        extractor = ClaudeVisionExtractor()
        reasoner = ClaudeExceptionReasoner()
    else:
        extractor = StubExtractor(_demo_invoice())
        reasoner = IdentityReasoner()
    return build_invoice_graph(
        extractor=extractor,
        reasoner=reasoner,
        ledger=Ledger(ledger_path),
        sink=build_sink(),
        store=build_legal_store(),
    )


def request_invoice_approval(graph, channel, registry, document, *, thread_id: str, phone: str):
    """Uruchamia dokument do bramki, rejestruje pending i wysyla request akceptacji.

    Zwraca payload (do akceptacji) lub None gdy graf sie nie zatrzymal (brak interrupt).
    Odpowiedz czlowieka domyka webhook: registry.resolve_oldest(numer) -> resume_document.
    """
    payload = start_document(graph, document, thread_id=thread_id)
    if payload is None:
        return None
    registry.add(thread_id, phone)
    channel.request_approval(payload, thread_id=thread_id)
    return payload


def persistent_checkpointer(db_path: str) -> SqliteSaver:
    """Trwaly checkpointer LangGraph (SQLite) — graf przezywa proces (async approve).

    check_same_thread=False: webhook (inny watek/proces) wznawia ten sam thread_id.
    serde z jawna allowlist (_CHECKPOINT_ALLOWED_TYPES): odporne na przyszle wersje
    LangGraph, ktore zablokuja deserializacje nieuznanych typow domyslnie.
    """
    serde = JsonPlusSerializer(allowed_msgpack_modules=_CHECKPOINT_ALLOWED_TYPES)
    conn = sqlite3.connect(db_path, check_same_thread=False)
    saver = SqliteSaver(conn, serde=serde)
    saver.setup()
    return saver
