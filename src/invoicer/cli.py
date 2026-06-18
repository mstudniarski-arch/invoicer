from __future__ import annotations

from collections.abc import Callable

from langgraph.types import Command

from invoicer.models import InvoiceDocument
from invoicer.state import InvoiceState


def process_document(
    graph,
    document: InvoiceDocument,
    *,
    thread_id: str,
    decide: Callable[[dict], str],
) -> InvoiceState:
    """Przeprowadza jeden dokument przez graf z bramka czlowieka.

    `decide(payload) -> "approve" | "reject"` dostaje podsumowanie z human_review.
    Domyslna implementacja CLI (Rich) wstrzykiwana jest przez wolajacego.
    """
    config = {"configurable": {"thread_id": thread_id}}
    result = graph.invoke({"document": document, "errors": []}, config)
    interrupts = result.get("__interrupt__")
    if interrupts:
        payload = interrupts[0].value
        result = graph.invoke(Command(resume=decide(payload)), config)
    return result
