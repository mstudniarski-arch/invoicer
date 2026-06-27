from __future__ import annotations

import math

from invoicer.ports import Embedder
from invoicer.rag.corpus import Chunk
from invoicer.rag.models import RetrievedChunk


def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(x * x for x in b))
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)


class InMemoryLegalStore:
    """Wektorowy store w pamieci (cosine brute-force) — fake do CI i lokalnych testow.

    Implementuje kontrakt zapisu uzywany przez ingest_corpus: existing_hashes() + add(),
    oraz port LegalKnowledgeStore: search().
    """

    def __init__(self, embedder: Embedder) -> None:
        self._embedder = embedder
        self._rows: list[tuple[str, list[float], Chunk]] = []  # (content_hash, vector, chunk)
        self._hashes: set[str] = set()

    @classmethod
    def from_chunks(cls, chunks: list[Chunk], embedder: Embedder) -> InMemoryLegalStore:
        store = cls(embedder)
        vectors = embedder.embed_documents([c.text for c in chunks]) if chunks else []
        for chunk, vector in zip(chunks, vectors, strict=True):
            store.add(chunk.content_hash, vector, chunk)
        return store

    def existing_hashes(self) -> set[str]:
        return set(self._hashes)

    def add(self, content_hash: str, embedding: list[float], chunk: Chunk) -> None:
        if content_hash in self._hashes:
            return
        self._hashes.add(content_hash)
        self._rows.append((content_hash, embedding, chunk))

    def search(self, query: str, k: int = 5) -> list[RetrievedChunk]:
        if not self._rows:
            return []
        q = self._embedder.embed_query(query)
        scored = [(_cosine(q, vector), chunk) for _, vector, chunk in self._rows]
        scored.sort(key=lambda item: item[0], reverse=True)
        return [
            RetrievedChunk(
                source_id=chunk.source_id,
                article_ref=chunk.article_ref,
                title=chunk.title,
                url=chunk.url,
                text=chunk.text,
                score=score,
            )
            for score, chunk in scored[:k]
        ]
