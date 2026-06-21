from __future__ import annotations

from collections.abc import Callable

from invoicer.models import InvoiceDocument
from invoicer.runner import resume_document, start_document
from invoicer.state import InvoiceState


def process_document(
    graph,
    document: InvoiceDocument,
    *,
    thread_id: str,
    decide: Callable[[dict], str],
) -> InvoiceState:
    """Przeprowadza jeden dokument przez graf z bramka czlowieka (CLI/sync).

    `decide(payload) -> "approve" | "reject"` dostaje podsumowanie z human_review.
    """
    payload = start_document(graph, document, thread_id=thread_id)
    if payload is None:  # graf nie zatrzymal sie (brak interrupt) — zwroc biezacy stan
        return graph.get_state({"configurable": {"thread_id": thread_id}}).values
    return resume_document(graph, thread_id=thread_id, decision=decide(payload))
