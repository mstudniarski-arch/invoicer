from __future__ import annotations

from collections.abc import Callable

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph

from invoicer.adapters.stub_reasoner import IdentityReasoner
from invoicer.graph.nodes import (
    classify_node,
    human_review,
    make_book_node,
    make_extract_node,
    make_reason_exception_node,
    make_validate_node,
    route_after_classify,
    route_after_review,
)
from invoicer.ledger import Ledger
from invoicer.ports import AccountingSink, ExceptionReasoner, InvoiceExtractor
from invoicer.state import InvoiceState


def build_invoice_graph(
    *,
    extractor: InvoiceExtractor,
    ledger: Ledger,
    sink: AccountingSink,
    reasoner: ExceptionReasoner | None = None,
    clock: Callable[[], str] | None = None,
    checkpointer=None,
):
    """Montuje graf: extract -> validate -> classify -> [reason_exception?] -> human_review -> book.

    Faktura zagraniczna przechodzi przez reason_exception (sedzia-LLM); PL prosto do human_review.
    Domyslny reasoner to IdentityReasoner (no-op) — graf dziala bez realnego LLM.
    Wymaga checkpointera (HITL/interrupt); domyslnie InMemorySaver.
    """
    reasoner = reasoner or IdentityReasoner()
    builder = StateGraph(InvoiceState)
    builder.add_node("extract", make_extract_node(extractor))
    builder.add_node("validate", make_validate_node(ledger))
    builder.add_node("classify", classify_node)
    builder.add_node("reason_exception", make_reason_exception_node(reasoner))
    builder.add_node("human_review", human_review)
    builder.add_node("book", make_book_node(sink, ledger, clock=clock))

    builder.add_edge(START, "extract")
    builder.add_edge("extract", "validate")
    builder.add_edge("validate", "classify")
    builder.add_conditional_edges(
        "classify",
        route_after_classify,
        {"reason_exception": "reason_exception", "human_review": "human_review"},
    )
    builder.add_edge("reason_exception", "human_review")
    builder.add_conditional_edges("human_review", route_after_review, {"book": "book", "end": END})
    builder.add_edge("book", END)

    return builder.compile(checkpointer=checkpointer or InMemorySaver())
