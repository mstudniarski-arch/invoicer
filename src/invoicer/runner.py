from __future__ import annotations

import os
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

from langgraph.types import Command

from invoicer.adapters.mock_subiekt import MockSubiektSink
from invoicer.adapters.stub_extractor import StubExtractor
from invoicer.adapters.stub_reasoner import IdentityReasoner
from invoicer.graph.build import build_invoice_graph
from invoicer.ledger import Ledger
from invoicer.models import Invoice, InvoiceDocument, LineItem, Party
from invoicer.state import InvoiceState


def start_document(graph, document: InvoiceDocument, *, thread_id: str) -> dict | None:
    """Uruchamia dokument w grafie do bramki human_review; zwraca payload interrupt (lub None)."""
    config = {"configurable": {"thread_id": thread_id}}
    result = graph.invoke({"document": document, "errors": []}, config)
    interrupts = result.get("__interrupt__")
    return interrupts[0].value if interrupts else None


def resume_document(graph, *, thread_id: str, decision: str) -> InvoiceState:
    """Wznawia graf po decyzji czlowieka (approve/reject/edit)."""
    config = {"configurable": {"thread_id": thread_id}}
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
        extractor=extractor, reasoner=reasoner, ledger=Ledger(ledger_path), sink=MockSubiektSink()
    )
