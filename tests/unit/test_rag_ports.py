from __future__ import annotations

from invoicer.ports import Embedder, LegalKnowledgeStore
from invoicer.rag.models import RetrievedChunk


class _Emb:
    def embed_documents(self, texts):
        return [[0.0] for _ in texts]

    def embed_query(self, text):
        return [0.0]


class _Store:
    def search(self, query, k=5):
        return [RetrievedChunk(source_id="s", article_ref="a", title="t", url="u", text="x")]


def test_embedder_protocol_is_runtime_checkable():
    assert isinstance(_Emb(), Embedder)


def test_legal_store_protocol_is_runtime_checkable():
    assert isinstance(_Store(), LegalKnowledgeStore)
