from __future__ import annotations

from datetime import date
from decimal import Decimal

from invoicer.adapters.fake_embedder import DeterministicEmbedder
from invoicer.adapters.in_memory_legal_store import InMemoryLegalStore
from invoicer.graph.nodes import make_retrieve_legal_context_node
from invoicer.models import Invoice, LineItem, Party
from invoicer.rag.corpus import Chunk
from invoicer.rag.query import build_retrieval_query


def _foreign_invoice() -> Invoice:
    line = LineItem(
        description="Subskrypcja SaaS",
        quantity=Decimal("1"),
        unit_net=Decimal("1000.00"),
        vat_rate=Decimal("0.00"),
        net=Decimal("1000.00"),
        vat=Decimal("0.00"),
        gross=Decimal("1000.00"),
    )
    return Invoice(
        seller=Party(name="Foreign Ltd", country="GB", vat_id="GB1"),
        buyer=Party(name="K"),
        number="INV/7",
        issue_date=date(2026, 6, 1),
        currency="GBP",
        lines=[line],
        total_net=Decimal("1000.00"),
        total_vat=Decimal("0.00"),
        total_gross=Decimal("1000.00"),
    )


def _chunk(text):
    return Chunk(
        source_id="vat-art-28b",
        article_ref="art. 28b ust. 1",
        title="t",
        url="u",
        kind="ustawa",
        text=text,
    )


def test_retrieve_returns_relevant_chunks_above_threshold():
    inv = _foreign_invoice()
    # Chunk o tresci == query -> cosine 1.0 (DeterministicEmbedder) -> ponad progiem.
    relevant = _chunk(build_retrieval_query(inv))
    noise = _chunk("zupelnie inny tekst o czyms innym")
    store = InMemoryLegalStore.from_chunks([relevant, noise], DeterministicEmbedder(dim=64))
    node = make_retrieve_legal_context_node(store, k=5)
    update = node({"invoice": inv})
    assert [c.article_ref for c in update["legal_context"]] == ["art. 28b ust. 1"]
    assert update["legal_context"][0].score > 0.99


def test_retrieve_empty_when_nothing_relevant():
    inv = _foreign_invoice()
    store = InMemoryLegalStore.from_chunks([_chunk("nic wspolnego")], DeterministicEmbedder(dim=64))
    node = make_retrieve_legal_context_node(store, k=5)
    update = node({"invoice": inv})
    assert update["legal_context"] == []  # ponizej progu -> pusto -> abstention dalej
