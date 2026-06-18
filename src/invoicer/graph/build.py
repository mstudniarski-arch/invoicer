from __future__ import annotations

from collections.abc import Callable

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph

from invoicer.graph.nodes import (
    classify_node,
    human_review,
    make_book_node,
    make_extract_node,
    make_validate_node,
    route_after_review,
)
from invoicer.ledger import Ledger
from invoicer.ports import AccountingSink, InvoiceExtractor
from invoicer.state import InvoiceState


def build_invoice_graph(
    *,
    extractor: InvoiceExtractor,
    ledger: Ledger,
    sink: AccountingSink,
    clock: Callable[[], str] | None = None,
    checkpointer=None,
):
    """Montuje graf: extract -> validate -> classify -> human_review -> (book | end).

    Wymaga checkpointera (HITL/interrupt); domyslnie InMemorySaver.
    """
    builder = StateGraph(InvoiceState)
    builder.add_node("extract", make_extract_node(extractor))
    builder.add_node("validate", make_validate_node(ledger))
    builder.add_node("classify", classify_node)
    builder.add_node("human_review", human_review)
    builder.add_node("book", make_book_node(sink, ledger, clock=clock))

    builder.add_edge(START, "extract")
    builder.add_edge("extract", "validate")
    builder.add_edge("validate", "classify")
    builder.add_edge("classify", "human_review")
    builder.add_conditional_edges("human_review", route_after_review, {"book": "book", "end": END})
    builder.add_edge("book", END)

    return builder.compile(checkpointer=checkpointer or InMemorySaver())
