from __future__ import annotations

from collections.abc import Callable

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph

from invoicer.adapters.fake_embedder import DeterministicEmbedder
from invoicer.adapters.in_memory_legal_store import InMemoryLegalStore
from invoicer.adapters.stub_reasoner import IdentityReasoner
from invoicer.graph.nodes import (
    classify_node,
    human_review,
    make_book_node,
    make_extract_node,
    make_reason_exception_node,
    make_retrieve_legal_context_node,
    make_validate_node,
    make_verify_grounding_node,
    route_after_classify,
    route_after_review,
    route_after_validate,
)
from invoicer.ledger import Ledger
from invoicer.ports import AccountingSink, ExceptionReasoner, InvoiceExtractor, LegalKnowledgeStore
from invoicer.state import InvoiceState


def build_invoice_graph(
    *,
    extractor: InvoiceExtractor,
    ledger: Ledger,
    sink: AccountingSink,
    reasoner: ExceptionReasoner | None = None,
    store: LegalKnowledgeStore | None = None,
    clock: Callable[[], str] | None = None,
    checkpointer=None,
):
    """Montuje graf. Galaz zagraniczna: classify -> retrieve_legal_context -> reason_exception
    (grounded) -> verify_grounding -> human_review. PL prosto do human_review.

    Domyslny reasoner: IdentityReasoner. Domyslny store: pusty InMemoryLegalStore -> brak kontekstu
    -> abstention (graf dziala bez realnego RAG/LLM). Wymaga checkpointera (interrupt).
    """
    reasoner = reasoner or IdentityReasoner()
    store = store or InMemoryLegalStore(DeterministicEmbedder())
    builder = StateGraph(InvoiceState)
    builder.add_node("extract", make_extract_node(extractor))
    builder.add_node("validate", make_validate_node(ledger))
    builder.add_node("classify", classify_node)
    builder.add_node("retrieve_legal_context", make_retrieve_legal_context_node(store))
    builder.add_node("reason_exception", make_reason_exception_node(reasoner))
    builder.add_node("verify_grounding", make_verify_grounding_node())
    builder.add_node("human_review", human_review)
    builder.add_node("book", make_book_node(sink, ledger, clock=clock))

    builder.add_edge(START, "extract")
    builder.add_edge("extract", "validate")
    builder.add_conditional_edges(
        "validate", route_after_validate, {"classify": "classify", "end": END}
    )
    builder.add_conditional_edges(
        "classify",
        route_after_classify,
        {"retrieve_legal_context": "retrieve_legal_context", "human_review": "human_review"},
    )
    builder.add_edge("retrieve_legal_context", "reason_exception")
    builder.add_edge("reason_exception", "verify_grounding")
    builder.add_edge("verify_grounding", "human_review")
    builder.add_conditional_edges("human_review", route_after_review, {"book": "book", "end": END})
    builder.add_edge("book", END)

    return builder.compile(checkpointer=checkpointer or InMemorySaver())
