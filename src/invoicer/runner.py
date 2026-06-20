from __future__ import annotations

from langgraph.types import Command

from invoicer.models import InvoiceDocument
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
